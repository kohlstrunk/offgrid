"""Offgrid v0.9.9: Beaufsichtigte, lokal freizugebende BHKW-Planumsetzung.

Aktoren nur nach lokaler AUTO_EIN-Freigabe. RE_W/RE_P nur auf Status-Port 1235,
NICHT auf der bestehenden Funk-Bridge 1234.
"""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from dataclasses import asdict
from datetime import timezone
from pathlib import Path
import time

from parameter import Parameter, AENDERBAR, lade_parameter, speichere_parameter, pruefen
from dataclasses import replace
from pv_cache import PVCache
from sensor_cache import SensorCache, PARSER
from energiebilanz import Energiebilanz
from thermik_live import prognose_thermik
from planung_automatik import plan_ermitteln
from pv_cache import atomar_speichern
from manueller_plan import ManuellerPlan
from energie_boost import EnergieBoost
from bhkw_steuerung import BHKWSteuerung
from bhkw_automatik import BHKWAutomatik
from wp_automatik import WPAutomatik

LOG = logging.getLogger('offgrid')


class Dienst:
    def __init__(self, host='127.0.0.1', port=1234, status_port=1235,
                 datenordner='daten', intervall=None, funkpause=None, timeout=8,
                 pv_limit=12):
        self.host, self.port, self.status_port = host, port, status_port
        self.timeout = timeout
        self.cache = SensorCache()
        self.energie = Energiebilanz()
        self.datenordner = Path(datenordner)
        self.parameter_pfad = self.datenordner / 'regelparameter.json'
        try:
            self.parameter = lade_parameter(self.parameter_pfad)
        except (ValueError, OSError) as exc:
            raise RuntimeError('Ungueltige Parameterdatei %s: %s' % (self.parameter_pfad, exc))
        # Die zentralen Parameter sind die einzige persistente Konfiguration.
        # Optionale Konstruktorwerte nur fuer bestehende Test-/Einbettungsaufrufe.
        if intervall is not None or funkpause is not None:
            self.parameter = pruefen(replace(self.parameter, **dict(
                ([('sensor_max_alter_s', int(intervall))] if intervall is not None else []) +
                ([('min_funkpause_s', int(funkpause))] if funkpause is not None else []))))
        self.letzte_thermik = None
        self._letzte_thermik_berechnung = None
        self._letzter_plan = None
        self._letzte_plan_berechnung = None
        self._letzter_plan_fingerprint = None
        # Planberechnung laeuft im Thread und wird gegen parallele Aufrufe geschuetzt.
        self._plan_lock = asyncio.Lock()
        self.manuell = ManuellerPlan(self.datenordner / 'manueller_plan.json')
        self.boost = EnergieBoost(self.datenordner / 'energie_boost.json')
        self.pv = PVCache(Path(datenordner) / 'pv_cache.json', limit=pv_limit)
        self.reader = self.writer = None
        self.letzte_anfrage = {name: float('-inf') for name in PARSER}
        self.letztes_senden = float('-inf')
        self.ausstehend = None
        self.ausstehend_seit = 0
        self.connected = False
        self.funk_lock = asyncio.Lock()  # alle Funksendungen zwischen Poller und Aktor koordinieren
        self.betriebsmodus = "SIMULATION"
        self.aktorfreigabe = False  # RE_START/RE_STOP geben keine Aktoren frei
        self.bhkw = BHKWSteuerung(self)
        self.automatik = BHKWAutomatik(self)
        self.wp_auto = WPAutomatik(self)

    def modus_text(self):
        if self.automatik.aktiv():
            return 'BHKW- UND WP-AUTOMATIK LOKAL FREIGEGEBEN'
        if self.betriebsmodus == 'SIMULATION':
            return 'SIMULATION'
        return 'AKTIV ANGEFORDERT (AKTOREN GESPERRT)'

    @property
    def intervall(self):
        return self.parameter.sensor_max_alter_s

    @property
    def funkpause(self):
        return self.parameter.min_funkpause_s

    async def empfangen(self):
        while True:
            zeile = await self.reader.readline()
            if not zeile:
                raise ConnectionError('Bridge getrennt')
            name = self.cache.aufnehmen(zeile.decode('utf-8', errors='replace').strip())
            if name:
                # Auch Antworten, die gui.py ausgelöst hat, gehen in die Bilanz ein.
                self.energie.aufnehmen(name, self.cache._werte[name][0])
                self.messwert_protokoll(name)
                if self.ausstehend == name:
                    self.ausstehend = None
                if name == 'MO_W':
                    # Lokaler START wartet unter Zustandslock auf frische MO_W/BM_W.
                    # Empfang darf deswegen NIEMALS auf motor_neu() blockieren.
                    # Einziger Dienst-Loop; Motorstatus sofort verarbeiten.
                    # Kein Hintergrundtask, dessen Lock-Warten unbemerkt scheitert.
                    try:
                        await self.bhkw.motor_neu()
                    except Exception:
                        LOG.exception('MO_W-BHKW-Zustandsverarbeitung fehlgeschlagen')
                        self.bhkw.arm_bis = 0.0
                        self.bhkw.zustand = 'FEHLER'
                        self.bhkw.fehler = 'Motorzustandsverarbeitung fehlgeschlagen; keine neuen Starts'
                        self.bhkw._log('MOTORSTATUS_FEHLER')
                if name == 'TM_B':
                    self._letzte_thermik_berechnung = None
                LOG.info('Sensor %s aktualisiert', name)

    async def abfragen(self):
        while True:
            await asyncio.sleep(1)
            async with self.funk_lock:
                jetzt = time.monotonic()
                if self.writer is None or (hasattr(self.writer, "is_closing") and self.writer.is_closing()):
                    continue
                if self.ausstehend:
                    if jetzt - self.ausstehend_seit < self.timeout:
                        continue
                    LOG.warning('Timeout bei %s (kein sofortiger Retry)', self.ausstehend)
                    self.ausstehend = None
                if jetzt - self.letztes_senden < self.funkpause:
                    continue
                # Laufender Motor meldet MO_W selbst alle 5 Minuten.
                # Weder 30-Sekunden-Polling noch regulaere MO_W-Polling-Anfragen
                # waehrend LAEUFT (auch bei veralteter Meldung).
                motor_laeuft = (self.bhkw.zustand in ('LAEUFT', 'LAEUFT_NACH_STOP') or
                                self.cache.motorstatus(7 * 60)['zustand'] == 'LAEUFT')
                name = None
                for kandidat in PARSER:
                    if kandidat == 'MO_W' and motor_laeuft:
                        continue
                    # Bei aktiver WP ausreichend oft nachmessen, um die
                    # 90%- und Temperatur-Abschaltgrenze zeitnah zu erkennen.
                    messintervall = (120 if self.wp_auto.an and kandidat in ('TM_B', 'IN_W', 'BM_W')
                                    else self.intervall)
                    if (self.cache.veraltet(kandidat, messintervall) and
                            jetzt - self.letzte_anfrage[kandidat] >= max(messintervall, 60)):
                        name = kandidat
                        break
                if name is not None:
                    self.writer.write((name + '\n').encode('ascii'))
                    await self.writer.drain()
                    self.letzte_anfrage[name] = jetzt
                    self.letztes_senden = jetzt
                    self.ausstehend = name
                    self.ausstehend_seit = jetzt
                    LOG.info('Abfrage %s', name)

    def messwert_protokoll(self, name):
        """Nur gültige Sensorantworten; JSONL für spätere Modellkalibrierung."""
        try:
            self.datenordner.mkdir(parents=True, exist_ok=True)
            daten, _, zeit = self.cache._werte[name]
            ereignis = {'zeit_utc': zeit, 'sensor': name, 'werte': asdict(daten)}
            datei = self.datenordner / ('messwerte_' + datetime.now().strftime('%Y-%m-%d') + '.jsonl')
            with datei.open('a', encoding='utf-8') as f:
                f.write(json.dumps(ereignis, ensure_ascii=False) + '\n')
        except OSError as exc:
            LOG.warning('Messwertprotokoll nicht schreibbar: %s', exc)

    def thermik(self):
        if self.cache.veraltet('TM_B', self.intervall):
            return {'status': 'KEINE GUELTIGE TEMPERATUR', 'nur_diagnose': True}
        now = time.monotonic()
        if self._letzte_thermik_berechnung is None or now - self._letzte_thermik_berechnung >= 60:
            tm = self.cache._werte['TM_B'][0]
            self.letzte_thermik = prognose_thermik(tm.innen_c, tm.aussen_c, self.parameter, fenster_halbstunden=self.pv.status()['fenster_halbstunden'])
            self._letzte_thermik_berechnung = now
        return self.letzte_thermik

    def plan(self):
        # 5-Minuten-Neuberechnung; geaenderte Sensor-/PV-Werte duerfen schneller
        # verarbeitet werden. Keine zusaetzlichen Funk-/HTTP-Anfragen.
        now = time.monotonic()
        fingerprint = (tuple((n, self.cache._werte[n][2] if n in self.cache._werte else None)
                             for n in ('TM_B', 'IN_W', 'BM_W', 'MO_W')),
                       self.pv.state.get('abgerufen'),
                       tuple((k, (self.boost.aktiv() or {}).get(k)) for k in ('energie_kwh', 'start_utc', 'ende_utc')),
                       self.intervall,
                       tuple((n, self.cache.veraltet(n, self.intervall))
                             for n in ('TM_B', 'IN_W', 'BM_W', 'MO_W')))
        if (self._letzter_plan is None or self._letzte_plan_berechnung is None
                or now - self._letzte_plan_berechnung > 300
                or fingerprint != self._letzter_plan_fingerprint):
            new_plan = plan_ermitteln(self.cache, self.pv, self.parameter, boost=self.boost.aktiv())
            old = self._letzter_plan
            if old is None or old.get('auswahl') != new_plan.get('auswahl') or old.get('status') != new_plan.get('status'):
                self.plan_protokoll(new_plan)
            self._letzter_plan = new_plan
            self._letzte_plan_berechnung = now
            self._letzter_plan_fingerprint = fingerprint
        result = dict(self._letzter_plan)
        manuell = self.manuell.aktiv()
        result['manuelle_vorgabe'] = manuell
        result['boost'] = self.boost.aktiv()
        result['automatische_auswahl'] = result.get('auswahl')
        if manuell:
            n = manuell['schema']
            kandidat = next((e for e in result.get('szenarien', []) if e.get('anzahl') == n), None)
            result['auswahl'] = ({k: v for k, v in kandidat.items() if k != 'verlauf_stuendlich'}
                                 if kandidat is not None else {'anzahl': n, 'zulaessig': False})
            result['status'] = ('MANUELLER PLAN (SIMULATION)' if kandidat is not None
                                else 'MANUELLER PLAN, DATEN FEHLEN')
            result['begruendung'] = 'Manuelle Vorgabe hat Vorrang vor automatischer Auswahl; 24 h ab Setzen.'
            if kandidat is not None and not kandidat.get('zulaessig'):
                result['warnung'] = 'Vorgegebenes Schema verletzt Modellgrenzen: ' + '; '.join(kandidat.get('gruende', []))
            if kandidat is None:
                result['warnung'] = 'Keine belastbare Simulation fuer manuelles Schema; Daten pruefen.'
        return result

    def plan_protokoll(self, plan):
        try:
            self.datenordner.mkdir(parents=True, exist_ok=True)
            pfad = self.datenordner / ('planentscheidungen_' + datetime.now().strftime('%Y-%m-%d') + '.jsonl')
            # Nur Zusammenfassung protokollieren, keine langen Stundenverlaeufe.
            log = {k: v for k, v in plan.items() if k != 'szenarien'}
            with pfad.open('a', encoding='utf-8') as f:
                f.write(json.dumps(log, ensure_ascii=False) + '\n')
        except OSError as exc:
            LOG.warning('Planprotokoll nicht schreibbar: %s', exc)

    def bericht(self):
        return {'zeit': datetime.now().astimezone().isoformat(),
                'modus': self.modus_text(),
                'bridge_verbunden': self.connected,
                'ausstehend': self.ausstehend,
                'sensoren': self.cache.bericht(self.intervall),
                'batterien_gesamt': self.cache.gesamt_soc(self.intervall),
                'motorstatus': self.cache.motorstatus(self.intervall),
                'bhkw_teststeuerung': self.bhkw.status(), 'bhkw_automatik': self.automatik.status(), 'wp_automatik': self.wp_auto.status(),
                'pv': self.pv.status(),
                'energiebilanz': self.energie.bericht(),
                'thermik': self.thermik(),
                'fenster_waerme': self.pv.status()['fenster_waerme'],
                'fenster_halbstunden': self.pv.status()['fenster_halbstunden'],
                'bhkw_plan': self.plan(),
                'hinweise': ['SoC aus Entnahme seit Voll-Ladung (SD/En); bei unplausiblen Zählern null',
                             'Pp ist Notstrom-DC-Einspeisung, keine PV-Leistung',
                             'Pd ist DC und nicht gleich AC-Einspeisung',
                             'BHKW-Status aus Drehzahl und optional AAN/AMAUS/AAUS; keine Schaltfreigabe']}

    def bericht_kurz(self):
        """Kompakte, maschinenlesbare Uebersicht statt kompletter Diagnose."""
        from datetime import timedelta
        from zoneinfo import ZoneInfo
        sens = self.cache.bericht(self.intervall)
        def sensor(name, keys):
            entry = sens[name]
            values = entry.get('werte') or {}
            return {'gueltig': entry['gueltig'],
                    'alter_s': entry['alter_sekunden'],
                    'werte': {key: values.get(key) for key in keys}}

        pv = self.pv.status()
        heute = datetime.now(ZoneInfo('Europe/Berlin')).date()
        dates = (heute, heute + timedelta(days=1))
        forecasts = pv.get('prognosen_kwh', {})
        def tage(name):
            data = forecasts.get(name) or {}
            return {'heute_kwh': data.get(dates[0].isoformat()),
                    'morgen_kwh': data.get(dates[1].isoformat())}
        heat = pv.get('fenster_waerme') or {}
        return {
            'zeit': datetime.now().astimezone().isoformat(timespec='seconds'),
            'modus': self.modus_text(), 'bridge_verbunden': self.connected,
            'sensoren': {
                'TM_B': sensor('TM_B', ('innen_c', 'aussen_c')),
                'IN_W': sensor('IN_W', ('entnommen_kwh', 'abgabe_ac_w', 'batteriespannung_v')),
                'BM_W': sensor('BM_W', ('entnommen_kwh', 'abgabe_dc_w', 'spannung_v')),
                'MO_W': sensor('MO_W', ('drehzahl_rpm', 'kruemmer_c', 'leistung_w', 'minuten_seit_start', 'zustandskennung')),
            },
            'batterien_gesamt': {k: self.cache.gesamt_soc(self.intervall).get(k)
                                for k in ('gueltig', 'restenergie_kwh', 'soc_prozent')},
            'motorzustand': self.cache.motorstatus(self.intervall)['zustand'],
            'bhkw_teststeuerung': self.bhkw.status(), 'bhkw_automatik': self.automatik.status(), 'wp_automatik': self.wp_auto.status(),
            'pv': {'pv1_4kwp_38grad': tage('pv1_4kwp_38grad'),
                   'pv2_2_5kwp_20grad': tage('pv2_2_5kwp_20grad'),
                   'fenster_sued_2_2kwp_90grad': tage('fenster_sued_2_2kwp_90grad'),
                   'waerme_heute_kwh': (tage('fenster_sued_2_2kwp_90grad')['heute_kwh'] * 3
                                        if tage('fenster_sued_2_2kwp_90grad')['heute_kwh'] is not None else None),
                   'waerme_morgen_kwh': (tage('fenster_sued_2_2kwp_90grad')['morgen_kwh'] * 3
                                         if tage('fenster_sued_2_2kwp_90grad')['morgen_kwh'] is not None else None),
                   'anfragen_heute': pv.get('anfragen_heute'), 'limit': pv.get('limit')}
        }

    async def plan_ohne_blockieren(self):
        """Langsame 24h-Simulation nicht im asyncio-Ereignisloop ausfuehren."""
        async with self._plan_lock:
            return await asyncio.to_thread(self.plan)

    async def bericht_ohne_blockieren(self):
        """RE_W_ALL enthaelt ebenfalls eine Planberechnung."""
        async with self._plan_lock:
            return await asyncio.to_thread(self.bericht)

    async def status_client(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            cmd = line.decode(errors='replace').strip().upper()
            if cmd == 'RE_W':
                result = self.bericht_kurz()
            elif cmd == 'RE_W_ALL':
                result = await self.bericht_ohne_blockieren()
            elif cmd == 'RE_E':
                result = self.energie.bericht()
            elif cmd == 'RE_T':
                result = dict(self.thermik())
                result['fenster_waerme'] = self.pv.status()['fenster_waerme']
                result['hinweis_solar'] = 'Halbstunden-Waermeleistung der Suedfenster in Temperaturszenarien integriert; keine Steuerfreigabe'
            elif cmd == 'RE_PLAN':
                plan = await self.plan_ohne_blockieren()
                result = {key: plan.get(key) for key in
                          ('zeitpunkt', 'horizont_h', 'modus', 'status', 'auswahl', 'begruendung', 'gruende', 'hinweise', 'manuelle_vorgabe', 'automatische_auswahl', 'warnung', 'boost')}
                result['betriebsmodus'] = self.modus_text()
                result['bhkw_teststeuerung'] = self.bhkw.status()
                result['bhkw_automatik'] = self.automatik.status()
                result['vergleich'] = [{k: e.get(k) for k in
                    ('anzahl', 'zulaessig', 'gruende', 'minimum_soc_prozent', 'soc_ende_prozent',
                     'temperatur_min_c', 'temperatur_max_c')}
                    for e in plan.get('szenarien', [])]
            elif cmd == 'RE_BO' or cmd.startswith('RE_BO '):
                teile = cmd.split()
                if len(teile) == 1:
                    result = {'boost': self.boost.aktiv(),
                              'hinweis': 'RE_BO <kWh> setzt/ersetzt Boost; gleicher Wert bleibt unveraendert; RE_BO 0 loescht.'}
                elif len(teile) == 2:
                    try:
                        menge = float(teile[1].replace(',', '.'))
                        self.boost.setzen(menge)
                        self._letzter_plan = None
                        result = {'ergebnis': 'BOOST AKTUALISIERT', 'boost': self.boost.aktiv(),
                                  'hinweis': 'Nur Planungssimulation; kein Motorstart.'}
                    except (ValueError, OSError) as exc:
                        result = {'fehler': str(exc)}
                else:
                    result = {'fehler': 'Syntax: RE_BO [kWh]; RE_BO 0 loescht Boost'}
            elif cmd in ('RE_START', 'RE_STOP'):
                self.betriebsmodus = ('AKTIV' if cmd == 'RE_START' else 'SIMULATION')
                result = {'betriebsmodus': self.modus_text(),
                          'aktorfreigabe': self.automatik.aktiv(),
                          'bhkw_teststeuerung': self.bhkw.status(), 'bhkw_automatik': self.automatik.status(), 'wp_automatik': self.wp_auto.status(),
                          'hinweis': ('RE_START/RE_STOP schalten keine Aktoren; '
                                     'BHKW-Automatik nur ueber lokales AUTO_EIN/AUTO_AUS auf dem Pi. '
                                     'Beim Neustart immer Simulation.')}
            elif cmd in ('RE_PA', 'RE_P0', 'RE_P1', 'RE_P2', 'RE_P3', 'RE_P4', 'RE_P5'):
                if cmd == 'RE_PA':
                    self.manuell.aufheben()
                    result = {'ergebnis': 'AUTOMATISCHE PLANAUSWAHL', 'betriebsmodus': self.modus_text()}
                else:
                    self.automatik.aus()
                    result = {'ergebnis': 'MANUELLER PLAN',
                              'manuelle_vorgabe': self.manuell.setzen(int(cmd[-1])),
                              'betriebsmodus': self.modus_text()}
            elif cmd.startswith('RE_PLAN_SET'):
                teile = cmd.split()
                if len(teile) != 2 or teile[0] != 'RE_PLAN_SET' or teile[1] not in ('0','1','2','3','4','5'):
                    result = {'fehler': 'Syntax: RE_PLAN_SET 0..5'}
                else:
                    self.automatik.aus()
                    result = {'ergebnis': 'MANUELLE VORGABE GESETZT',
                              'manuelle_vorgabe': self.manuell.setzen(int(teile[1])),
                              'hinweis': 'Nur Simulation, keine BHKW-Befehle'}
            elif cmd == 'RE_PLAN_AUTO':
                self.manuell.aufheben()
                result = {'ergebnis': 'AUTOMATISCHE PLANAUSWAHL WIEDER AKTIV',
                          'hinweis': 'Nur Simulation, keine BHKW-Befehle'}
            elif cmd == 'RE_PLAN_ALL':
                result = await self.plan_ohne_blockieren()
            elif cmd == 'RE_P':
                result = {'parameter': {k: getattr(self.parameter, k) for k in sorted(AENDERBAR)},
                          'speicherdatei': str(self.parameter_pfad),
                          'hinweis': 'RE_SET <name> <wert>; nur Simulation, keine Aktorsteuerung',
                          'sensor_max_alter_s': self.intervall,
                          'min_funkpause_s': self.funkpause,

                          'manueller_plan': self.manuell.aktiv(), 'boost': self.boost.aktiv(), 'betriebsmodus': self.modus_text(), 'aktorfreigabe': self.automatik.aktiv(), 'bhkw_teststeuerung': self.bhkw.status(), 'bhkw_automatik': self.automatik.status()}
            elif cmd.startswith('RE_SET'):
                parts = cmd.split()
                if len(parts) != 3 or parts[0] != 'RE_SET':
                    result = {'fehler': 'Syntax: RE_SET <name> <wert>'}
                elif parts[1].lower() not in AENDERBAR:
                    result = {'fehler': 'Unbekannter/nicht aenderbarer Parameter: ' + parts[1]}
                else:
                    try:
                        key = parts[1].lower()
                        if key in ('sensor_max_alter_s', 'min_funkpause_s'):
                            raw = parts[2].strip()
                            if not raw.isdecimal():
                                raise ValueError(key + ' erfordert eine ganze Sekundenzahl')
                            value = int(raw)
                        else:
                            value = float(parts[2].replace(',', '.'))
                        neuer = pruefen(replace(self.parameter, **{parts[1].lower(): value}))
                        # Erst persistieren, dann wirksam machen.
                        speichere_parameter(self.parameter_pfad, neuer)
                        self.parameter = neuer
                        self._letzter_plan = None
                        self._letzte_thermik_berechnung = None
                        result = {'ergebnis': 'PARAMETER GESETZT', 'name': parts[1].lower(),
                                  'wert': value, 'dauerhaft': True,
                                  'hinweis': 'Plan wird bei naechster Abfrage neu berechnet; keine Aktorsteuerung'}
                    except (ValueError, OSError, TypeError) as exc:
                        result = {'fehler': str(exc)}
            else:
                result = {'fehler': 'RE_BO [kWh], RE_START, RE_STOP, RE_PA, RE_P0..RE_P5, RE_W, RE_P, RE_SET, RE_PLAN, RE_PLAN_ALL; Aktorbefehle gesperrt'}
            writer.write((json.dumps(result, ensure_ascii=False) + '\n').encode('utf-8'))
            await writer.drain()
        except Exception as exc:
            LOG.exception('Fehler bei Statusabfrage')
            try:
                writer.write((json.dumps({'fehler': 'Statusabfrage fehlgeschlagen',
                                          'typ': type(exc).__name__}, ensure_ascii=False) + '\n').encode('utf-8'))
                await writer.drain()
            except (OSError, ConnectionError):
                pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def lokaler_aktor_client(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            aktion = line.decode('ascii', errors='replace').strip().upper()
            if aktion == 'AUTO_EIN':
                status = self.automatik.ein()
            elif aktion == 'AUTO_AUS':
                status = self.automatik.aus()
            else:
                if aktion in ('STOP', 'START'):
                    # Ein manuell erteilter Befehl beendet den automatischen Modus.
                    self.automatik.aus()
                status = await self.bhkw.lokal(aktion)
            response = {'ok': True, 'bhkw_teststeuerung': self.bhkw.status(), 'bhkw_automatik': self.automatik.status(), 'wp_automatik': self.wp_auto.status()}
        except Exception as exc:
            LOG.exception('Lokaler BHKW-Testbefehl fehlgeschlagen')
            try:
                status = self.bhkw.status()
            except Exception:
                status = {'zustand': 'UNBEKANNT', 'automatische_planumsetzung': False}
            response = {'ok': False, 'fehler': str(exc), 'bhkw_teststeuerung': status}
        try:
            writer.write((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
            await writer.drain()
        except (OSError, ConnectionError):
            LOG.warning('Lokaler BHKW-Client hat Verbindung getrennt')
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    async def pv_loop(self):
        try:
            if await self.pv.beim_start():
                LOG.info('Fehlende PV-Prognosen beim Dienststart angefordert')
        except Exception:
            LOG.exception('PV-Startprüfung fehlgeschlagen')
        while True:
            try:
                await self.pv.faellig()
            except Exception:
                LOG.exception('PV-Cache-Fehler')
            await asyncio.sleep(60)

    async def bridge_loop(self):
        while True:
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                self.connected = True
                LOG.info('Bridge verbunden: %s:%d', self.host, self.port)
                # Zunächst passiv mithören; erst nach kurzer Schonfrist abfragen.
                empfang = asyncio.create_task(self.empfangen())
                await asyncio.sleep(10)
                abfragen = asyncio.create_task(self.abfragen())
                done, pending = await asyncio.wait([empfang, abfragen], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    task.result()
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                LOG.warning('Bridge nicht erreichbar: %s', exc)
            finally:
                self.connected = False
                self.ausstehend = None
                if self.writer:
                    self.writer.close()
                    try:
                        await self.writer.wait_closed()
                    except OSError:
                        pass
                self.reader = self.writer = None
            await asyncio.sleep(10)

    async def start(self):
        import os
        self.datenordner.mkdir(parents=True, exist_ok=True)
        self.bhkw_sock = self.datenordner / 'bhkw_lokal.sock'
        if self.bhkw_sock.exists():
            # Keine fremden Socket-/Datei-Pfade ueberschreiben.
            import stat
            if not stat.S_ISSOCK(self.bhkw_sock.lstat().st_mode):
                raise RuntimeError('Lokaler Steuer-Socketpfad ist keine Socketdatei')
            self.bhkw_sock.unlink()
        local_server = await asyncio.start_unix_server(self.lokaler_aktor_client, path=str(self.bhkw_sock))
        os.chmod(self.bhkw_sock, 0o600)
        server = await asyncio.start_server(self.status_client, '127.0.0.1', self.status_port)
        LOG.info('Lokale Statusschnittstelle: 127.0.0.1:%d', self.status_port)
        async with server, local_server:
            try:
                await asyncio.gather(server.serve_forever(), local_server.serve_forever(),
                                     self.bridge_loop(), self.pv_loop(), self.bhkw.ueberwachen(), self.automatik.loop(), self.wp_auto.loop())
            finally:
                self.bhkw_sock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='Offgrid v0.9.9 Planung; BHKW-Automatik lokal beaufsichtigt')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=1234)
    parser.add_argument('--status-port', type=int, default=1235)
    parser.add_argument('--datenordner', default='daten')
    parser.add_argument('--pv-limit', type=int, default=12)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    # Python 3.9: asyncio-Primitiven erst im aktiven Dienst-Event-Loop erzeugen.
    # Bei Konstruktion vor asyncio.run() koennen Locks einem anderen Loop
    # zugeordnet werden ('Future attached to a different loop').
    async def _run_dienst():
        dienst = Dienst(args.host, args.port, args.status_port, args.datenordner,
                       pv_limit=args.pv_limit)
        await dienst.start()

    asyncio.run(_run_dienst())


if __name__ == '__main__':
    main()
