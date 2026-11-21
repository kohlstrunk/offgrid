#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sys
import termios
import tty
import smtplib
import os
import json
from datetime import datetime
from email.mime.text import MIMEText
import serial_asyncio

TCP_PORT = 1234
SERIAL_PORT = "/dev/serial0"
SERIAL_BAUD = 9600


# ---------------------------------------------------------------------------
# E-MAIL BEI ALARM
# ---------------------------------------------------------------------------
async def send_alarm_email(reason: str):
    try:
        sender = "berndkohlstrunk0105@gmail.com"
        recipient = "bernd.kohlstrunk@web.de"
        app_password = os.environ.get("OFFGRID_SMTP_APP_PASSWORD")
        if not app_password:
            raise RuntimeError("OFFGRID_SMTP_APP_PASSWORD ist nicht gesetzt")
        
        msg = MIMEText(f"Alarm ausgelöst:\n\n{reason}")
        msg["Subject"] = "ALARM"
        msg["From"] = sender
        msg["To"] = recipient

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, app_password)
            server.sendmail(sender, [recipient], msg.as_string())

        print("[INFO] Alarm-Mail gesendet")

    except Exception as e:
        print(f"[FEHLER] Alarm-Mail: {e}")


# ---------------------------------------------------------------------------
# BYTE-TRANSPARENTE WEITERLEITUNG
# ---------------------------------------------------------------------------
async def forward_bytes(data: bytes, source, clients, serial_proto):
    # TCP
    if source != "tcp":
        for w in list(clients):
            try:
                w.write(data)
                await w.drain()
            except:
                clients.discard(w)

    # Serial
    if source != "serial":
        if serial_proto and serial_proto.transport:
            serial_proto.transport.write(data)

    # Console
    if source != "console":
        sys.stdout.buffer.write(data)
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# TERMINIERTER PARSER (nur Beobachter!)
# ---------------------------------------------------------------------------
parser_buffers = {
    "tcp": "",
    "serial": "",
    "console": ""
}

async def parse_for_commands(data: bytes, source, clients, serial_proto):
    text = data.decode(errors="ignore").replace("\r", "")
    buf = parser_buffers[source] + text

    while "\n" in buf:
        line, buf = buf.split("\n", 1)

        if "ALARM" in line:
            asyncio.create_task(send_alarm_email(line))

        if "RA_TI" in line:
            ts = datetime.now().strftime("TI_%Y-%m-%d %H:%M:%S.%f") + "\n"
            await forward_bytes(ts.encode(), "parser", clients, serial_proto)

    parser_buffers[source] = buf


# ---------------------------------------------------------------------------
# SERIAL PROTOKOLL (byte-transparent)
# ---------------------------------------------------------------------------
class SerialProtocol(asyncio.Protocol):
    def __init__(self, clients):
        self.clients = clients
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        print(f"[INFO] Serial verbunden ({SERIAL_PORT})")

    def data_received(self, data):
        asyncio.create_task(forward_bytes(data, "serial", self.clients, self))
        asyncio.create_task(parse_for_commands(data, "serial", self.clients, self))





# Eigenstaendige Formatierung fuer Port 1234.
# Kein Import aus status.py: Bridge kann in einem anderen Verzeichnis laufen.
def format_kurz(data):
    """Kompakte Ausgabe mit deutlicher Kennzeichnung fehlender Daten."""
    def fmt(v, digits=1, unit=''):
        return '--' if v is None else ('%.*f%s' % (digits, v, unit))

    def zeile(name, formatter):
        s = data['sensoren'][name]
        age = '--' if s['alter_s'] is None else str(round(s['alter_s'])) + 's'
        status = 'OK' if s['gueltig'] else 'ALT/FEHLT'
        return '%-5s [%s, %s] %s' % (name, status, age,
                formatter(s['werte']) if s['gueltig'] else '(keine aktuellen Werte)')

    rows = [data['zeit'] + '  Bridge: ' + ('OK' if data['bridge_verbunden'] else 'GETRENNT') + '  (' + data['modus'] + ')',
            zeile('TM_B', lambda v: 'Innen %s  Aussen %s' % (fmt(v['innen_c'], 1, ' C'), fmt(v['aussen_c'], 1, ' C'))),
            zeile('IN_W', lambda v: 'SD %s  Pw %s  Ubat %s' % (fmt(v['entnommen_kwh'], 2, ' kWh'), fmt(v['abgabe_ac_w'], 0, ' W'), fmt(v['batteriespannung_v'], 1, ' V'))),
            zeile('BM_W', lambda v: 'En %s  Pd %s  U %s' % (fmt(v['entnommen_kwh'], 2, ' kWh'), fmt(v['abgabe_dc_w'], 0, ' W'), fmt(v['spannung_v'], 1, ' V'))),
            zeile('MO_W', lambda v: '%s rpm  Tk %s  Pel %s  Lauf %s' % (fmt(v['drehzahl_rpm'], 0), fmt(v['kruemmer_c'], 1, ' C'), fmt(v['leistung_w'], 0, ' W'), fmt(v['minuten_seit_start'], 0, ' min'))),
            'BHKW: ' + data['motorzustand']]
    soc = data['batterien_gesamt']
    rows.append('Akku: ' + (('%s / 27 kWh  SOC %s' % (fmt(soc['restenergie_kwh'], 2, ' kWh'), fmt(soc['soc_prozent'], 1, '%'))) if soc['gueltig'] else 'ungueltig'))
    rows.append('PV-Prognose          heute / morgen (kWh):')
    for name, label in [('pv1_4kwp_38grad', '  PV 4kWp/38'), ('pv2_2_5kwp_20grad', '  PV 2.5kWp/20'), ('fenster_sued_2_2kwp_90grad', '  Fenster virtuell')]:
        v = data['pv'][name]
        rows.append('%-22s %s / %s' % (label, fmt(v['heute_kwh'], 2), fmt(v['morgen_kwh'], 2)))
    rows.append('  Fensterwaerme:        %s / %s kWh' % (fmt(data['pv']['waerme_heute_kwh'], 2), fmt(data['pv']['waerme_morgen_kwh'], 2)))
    rows.append('PV-Abfragen: %s/%s heute' % (data['pv']['anfragen_heute'], data['pv']['limit']))
    return '\n'.join(rows)

def format_plan(data):
    """Kompakte BHKW-Planung; keine automatischen Schaltbefehle."""
    def num(value, unit='', digits=1):
        if value is None:
            return '--'
        return ('%.*f%s' % (digits, value, unit))

    rows = ['BHKW-PLAN  %s  (%s)' % (data.get('zeitpunkt', '--'), data.get('betriebsmodus', 'SIMULATION')),
            'Status: %s' % data.get('status', '--')]
    manual = data.get('manuelle_vorgabe')
    if manual:
        rows.append('MANUELL: Schema %s bis %s UTC' % (manual['schema'], manual['gueltig_bis_utc']))
    boost = data.get('boost')
    if boost:
        rows.append('Boost: %.2f kWh Zusatzverbrauch (Rest %.2f kWh, bis %s)' % (
            boost['energie_kwh'], boost['rest_kwh'], boost['ende_utc']))
    if data.get('warnung'):
        rows.append('WARNUNG: ' + data['warnung'])
    chosen = data.get('auswahl')
    if chosen:
        rows.append('Auswahl: %s Starts/Tag | kommende Starts: %s' % (
            chosen.get('anzahl', '--'),
            ', '.join(x[11:16] + ('+' + x[:10] if x[:10] != str(data.get('zeitpunkt',''))[:10] else '')
                      for x in chosen.get('startzeiten_im_horizont', [])) or 'keine'))
        rows.append('Prognose: SOC Ende %s  (Minimum %s)  Temp %s bis %s' % (
            num(chosen.get('soc_ende_prozent'), '%'),
            num(chosen.get('minimum_soc_prozent'), '%'),
            num(chosen.get('temperatur_min_c'), ' C'),
            num(chosen.get('temperatur_max_c'), ' C')))
        ersatz = chosen.get('ersatzprofile') or []
        fenster = [v for v in ersatz if v.startswith('fenster_sued_2_2kwp_90grad/')]
        if fenster:
            tage = ', '.join(v.split('/', 1)[1] for v in fenster)
            rows.append('Hinweis: Fensterwaerme zeitlich geschaetzt (%s); Tagesprognose vorhanden.' % tage)
    elif data.get('gruende'):
        rows.append('Grund: ' + '; '.join(str(v) for v in data['gruende']))
    rows.append('Schema | OK | SOC Ende | SOC min | Temperatur')
    for e in data.get('vergleich', []):
        rows.append('  %s    | %s | %s | %s | %s..%s C' % (
            e.get('anzahl','?'), 'ja' if e.get('zulaessig') else 'nein',
            num(e.get('soc_ende_prozent'), '%'),
            num(e.get('minimum_soc_prozent'), '%'),
            num(e.get('temperatur_min_c')), num(e.get('temperatur_max_c'))))
    if not chosen and data.get('vergleich'):
        reasons = []
        for e in data['vergleich']:
            reasons.extend(e.get('gruende') or [])
        if reasons:
            rows.append('Ablehnungen: ' + '; '.join(dict.fromkeys(reasons))[:240])
    return '\n'.join(rows)

# RE_-Befehle werden direkt an den lokalen Regelungsdienst auf 1235 geroutet.
# Ihre Antworten gehen an alle TCP-Clients und die Konsole, NICHT an 868 MHz.
async def broadcast_re_answer(message, clients):
    """RE-Antwort ausschliesslich an alle verbundenen TCP-Clients und Konsole.

    Niemals ueber die serielle Schnittstelle / 868 MHz senden.
    """
    data = (message.rstrip("\r\n") + "\n").encode("utf-8")
    for client in list(clients):
        try:
            client.write(data)
            await client.drain()
        except (ConnectionError, OSError, RuntimeError):
            clients.discard(client)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


async def dispatch_re_command(line, clients):
    """RE-Befehl an Port 1235 und Ergebnis an ALLE TCP-Clients verteilen."""
    try:
        reader_re, writer_re = await asyncio.wait_for(
            asyncio.open_connection('127.0.0.1', 1235), timeout=3)
        try:
            writer_re.write(line + b'\n')
            await writer_re.drain()
            answer = await asyncio.wait_for(reader_re.readline(), timeout=120)
            if not answer:
                raise RuntimeError('Regelungsdienst hat nicht geantwortet')
            data = json.loads(answer.decode('utf-8'))
            command = line.decode('ascii', errors='replace').strip().upper()
            if command == 'RE_W':
                message = format_kurz(data)
            elif command == 'RE_PLAN':
                message = format_plan(data)
            else:
                message = json.dumps(data, indent=2, ensure_ascii=False)
        finally:
            writer_re.close()
            await writer_re.wait_closed()
    except Exception as exc:
        message = '[RE] Fehler: %s' % exc
    await broadcast_re_answer(message, clients)


# ---------------------------------------------------------------------------
# TCP HANDLER (robust, byte-transparent)
# ---------------------------------------------------------------------------
async def handle_tcp_client(reader, writer, clients, serial_proto):
    addr = writer.get_extra_info("peername")
    print(f"[INFO] TCP verbunden: {addr}")
    clients.add(writer)

    buffer = b''
    try:
        while True:
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=3600)
            except asyncio.TimeoutError:
                print(f"[INFO] TCP Timeout: {addr}")
                break

            if not data:
                break
                                   # source
            # Nachrichten sind im Hausprotokoll zeilenorientiert.
            # Fragmentierte TCP-Pakete werden zusammengefuehrt.
            buffer += data
            if len(buffer) > 8192 and b'\n' not in buffer:
                # Fuer Nicht-RE-Bytefolgen transparente Weiterleitung beibehalten.
                if not buffer.startswith(b'RE_'):
                    await forward_bytes(buffer, "tcp", clients, serial_proto)
                    await parse_for_commands(buffer, "tcp", clients, serial_proto)
                    buffer = b''
                else:
                    await broadcast_re_answer('[RE] Fehler: Befehlszeile zu lang', clients)
                    buffer = b''
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if line.strip().upper().startswith(b'RE_'):
                    await dispatch_re_command(line.strip(), clients)
                else:
                    original = line + b'\n'
                    await forward_bytes(original, "tcp", clients, serial_proto)
                    await parse_for_commands(original, "tcp", clients, serial_proto)

    except ConnectionResetError:
        print(f"[INFO] TCP hart getrennt: {addr}")

    finally:
        clients.discard(writer)
 #       writer.close()
 #       await writer.wait_closed()
 #      besser hier den Fehler abfangen
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            print(f"[TCP] Fehler beim Schließen {addr}: {e}")

        print(f"[INFO] TCP getrennt: {addr}")

# ---------------------------------------------------------------------------
# KONSOLE (byte-transparent, lokales Echo), gepuffert
# ---------------------------------------------------------------------------
async def console_reader(clients, serial_proto):
    loop = asyncio.get_running_loop()

    old = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())

    buffer = b""

    try:
        while True:
            ch = await loop.run_in_executor(None, sys.stdin.buffer.read, 1)
            if not ch:
                continue

            # lokales Echo
            sys.stdout.buffer.write(ch)
            sys.stdout.flush()

            # Terminator?
            if ch in (b"\n", b"\r"):
                # CRLF sauber behandeln
                if ch == b"\r":
                    next_ch = await loop.run_in_executor(
                        None, sys.stdin.buffer.peek, 1
                    )
                    if next_ch.startswith(b"\n"):
                        await loop.run_in_executor(None, sys.stdin.buffer.read, 1)

                if buffer:
                    line = buffer + b"\n"

                    # erst JETZT senden
                    if line.strip().upper().startswith(b'RE_'):
                        await dispatch_re_command(line.strip(), clients)
                    else:
                        await forward_bytes(line, "console", clients, serial_proto)
                        await parse_for_commands(line, "console", clients, serial_proto)

                    buffer = b""

            else:
                buffer += ch

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
async def main():
    clients = set()
    loop = asyncio.get_running_loop()

    # Serial
    transport, serial_proto = await serial_asyncio.create_serial_connection(
        loop, lambda: SerialProtocol(clients), SERIAL_PORT, SERIAL_BAUD
    )

    # TCP Server
    server = await asyncio.start_server(
        lambda r, w: handle_tcp_client(r, w, clients, serial_proto),
        "0.0.0.0",
        TCP_PORT
    )
    print(f"[INFO] TCP Server läuft auf Port {TCP_PORT}")

    # Konsole
    asyncio.create_task(console_reader(clients, serial_proto))

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[INFO] Beendet.")
