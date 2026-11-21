"""Lokale Statusanfrage; Beispiel python3 status.py RE_W."""
import argparse
import asyncio
import json


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
        if chosen.get('heizdefizit_notplan'):
            rows.append('HEIZDEFIZIT: Mindesttemperatur nicht erreichbar; maximaler sicherer Heizplan.')
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


async def lesen(befehl, port, timeout=90):
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    try:
        writer.write((befehl + '\n').encode())
        await writer.drain()
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            raise RuntimeError('Keine Antwort vom Dienst innerhalb von %d Sekunden. '
                               'Bitte Ausgabe von dienst.py pruefen.' % timeout)
        if not data:
            raise RuntimeError('Dienst hat Verbindung ohne Antwort geschlossen.')
        return json.loads(data)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument('befehl', help='RE_PLAN_SET 0..5, RE_PLAN_AUTO, RE_PLAN, ...')
    p.add_argument('schema', nargs='?', help='0 bis 5 bei RE_PLAN_SET; Name bei RE_SET')
    p.add_argument('wert', nargs='?', help='Wert bei RE_SET')
    p.add_argument('--port', type=int, default=1235)
    p.add_argument('--timeout', type=int, default=90, help='Antwort-Timeout in Sekunden (Standard 90)')
    args = p.parse_args()
    erlaubte = {'RE_W', 'RE_W_ALL', 'RE_P', 'RE_E', 'RE_T', 'RE_PLAN', 'RE_PLAN_ALL', 'RE_PLAN_AUTO', 'RE_PLAN_SET', 'RE_SET', 'RE_START', 'RE_STOP', 'RE_PA', 'RE_P0', 'RE_P1', 'RE_P2', 'RE_P3', 'RE_P4', 'RE_P5', 'RE_BO'}
    if args.befehl not in erlaubte:
        p.error('Unbekannter Befehl')
    if args.befehl == 'RE_PLAN_SET':
        if args.wert is not None:
            p.error('RE_PLAN_SET erwartet nur ein Argument')
        if args.schema not in ('0', '1', '2', '3', '4', '5'):
            p.error('RE_PLAN_SET erfordert Schema 0..5')
        befehl = args.befehl + ' ' + args.schema
    elif args.befehl == 'RE_BO':
        if args.wert is not None:
            p.error('RE_BO erwartet hoechstens eine kWh-Angabe')
        befehl = 'RE_BO' + (' ' + args.schema if args.schema is not None else '')
    elif args.befehl == 'RE_SET':
        if not args.schema or args.wert is None:
            p.error('RE_SET erfordert Parametername und Wert')
        befehl = 'RE_SET %s %s' % (args.schema, args.wert)
    else:
        if args.schema is not None or args.wert is not None:
            p.error('Unerwartetes Argument')
        befehl = args.befehl
    try:
        result = asyncio.run(lesen(befehl, args.port, args.timeout))
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        p.exit(1, 'Statusabfrage fehlgeschlagen: %s\n' % exc)
    if isinstance(result, dict) and result.get('fehler'):
        p.exit(1, 'Dienstfehler: %s\n' % result['fehler'])
    if args.befehl == 'RE_W':
        print(format_kurz(result))
    elif args.befehl == 'RE_PLAN':
        print(format_plan(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
