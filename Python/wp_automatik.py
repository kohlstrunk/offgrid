"""Prognosebasierte PV-Ueberschussheizung fuer die Insel-Waermepumpe.

Fuenf-Minuten-Energiebilanz bestimmt den Einschaltzeitpunkt; der feste
Einschalt-SOC wird nicht mehr verwendet. WP_H/WP_S ohne WP-Rueckkanal.
"""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
from pv_cache import atomar_speichern
import time
from zoneinfo import ZoneInfo
from wp_vorausschau import wp_start_prognose

TZ = ZoneInfo('Europe/Berlin')


class WPAutomatik:
    def __init__(self, dienst):
        self.d = dienst
        self.zustandspfad = Path(dienst.datenordner) / 'wp_aktorzustand.json'
        try:
            alt = json.loads(self.zustandspfad.read_text(encoding='utf-8'))
            self.modus = alt.get('wp_modus', 'HEIZEN' if alt.get('wp_heizauftrag_offen') else 'AUS')
            if self.modus not in ('AUS', 'HEIZEN', 'KUEHLEN'):
                raise ValueError('Ungueltiger gespeicherter WP-Modus')
            self.an = self.modus != 'AUS'
        except FileNotFoundError:
            self.an = False
            self.modus = 'AUS'
        except (ValueError, OSError, AttributeError) as exc:
            raise RuntimeError('WP-Aktorzustand ungeklärt: ' + str(exc))
        self.wiederanlauf_stop_noetig = self.an
        self.letztes_ereignis = 'NEUSTART_WP_STOP_NOETIG' if self.an else 'NEUSTART_WP_AUS'
        self.log_path = Path(dienst.datenordner) / 'wp_entscheidungen.jsonl'
        self.letzte_probe = None   # (monotonic, SD in kWh)
        self.ladetrend_kwh = None
        self.ladetrend_zeit = None
        self.letzter_befehl = 0.0
        self.letzter_pruefgrund = 'NOCH_NICHT_GEPRUEFT'
        self._vorher_aktiv = False
        self.wp_prognose = None
        self.wp_prognose_zeit = 0.0
        self.wp_prognose_tag = None
        self.wp_pv_abgerufen = None
        self.wp_plan_stop = None
        self.wp_heute_beendet = None

    def _log(self, art, **details):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(zeit=datetime.now(TZ).isoformat(), art=art,
                                    **details), ensure_ascii=False, default=str) + '\n')
        self.letztes_ereignis = art

    def status(self):
        return {'wp_heizung_beauftragt': self.an and self.modus == 'HEIZEN',
                'wp_kuehlung_beauftragt': self.an and self.modus == 'KUEHLEN',
                'wp_modus': self.modus,
                'wp_stop_nach_neustart_noetig': self.wiederanlauf_stop_noetig,
                'automatik_freigegeben': self.d.automatik.aktiv(),
                'soc_ein_prozent': self.d.parameter.wp_soc_ein_prozent,
                'soc_ein_verfahren': 'DYNAMISCH_AUS_PV_PROGNOSE',
                'vorausschau': self.wp_prognose,
                'wp_plan_stop': self.wp_plan_stop.isoformat() if self.wp_plan_stop else None,
                'soc_aus_prozent': self.d.parameter.wp_soc_aus_prozent,
                'ladetrend_kwh': self.ladetrend_kwh,
                'letztes_ereignis': self.letztes_ereignis,
                'letzter_pruefgrund': self.letzter_pruefgrund,
                'entscheidungsprotokoll': str(self.log_path)}

    def _tag_pv(self):
        tag = datetime.now(TZ).date().isoformat()
        daten = self.d.pv.state.get('prognosen_kwh', {})
        try:
            return float((daten.get('pv1_4kwp_38grad') or {}).get(tag) or 0)
        except (ValueError, TypeError):
            return 0.0

    def _sensoren(self):
        c = self.d.cache
        alter = min(self.d.intervall, 600)
        if any(c.veraltet(n, alter) for n in ('TM_B', 'IN_W', 'BM_W')):
            return None
        tm = c._werte['TM_B'][0]
        inv = c._werte['IN_W'][0]
        bm = c._werte['BM_W'][0]
        soc = c.gesamt_soc(alter)
        if not soc['gueltig']:
            return None
        return {'temp': tm.innen_c, 'soc': soc['soc_prozent'],
                'soc_insel': (20.0 - inv.entnommen_kwh) * 5,
                'sd': inv.entnommen_kwh, 'soc_klein': (7 - bm.entnommen_kwh) / 7 * 100}

    def _trend(self, sd, jetzt):
        # Nur NEUE IN_W-Daten verwenden, nicht wiederholt denselben Cachewert.
        timestamp = self.d.cache._werte['IN_W'][1]
        if self.letzte_probe is None:
            self.letzte_probe = (timestamp, sd)
            return
        alt_zeit, alt_sd = self.letzte_probe
        if timestamp <= alt_zeit:
            return
        # Ruecksetzen von SD bei Voll-Ladung darf keinen scheinbaren
        # massiven Ladetrend erzeugen.
        delta = alt_sd - sd
        self.letzte_probe = (timestamp, sd)
        if timestamp - alt_zeit >= 30 and 0 <= delta <= 2.0:
            self.ladetrend_kwh = round(delta, 4)
            self.ladetrend_zeit = jetzt
        else:
            self.ladetrend_kwh = None
            self.ladetrend_zeit = None

    async def _senden(self, cmd, art, **info):
        # Vor dem Funkbefehl den offenen Auftrag persistent speichern: Nach
        # Dienst-Neustart erfolgt ein einzelner WP_S-Befehl fuer beide Modi.
        if cmd in ('WP_H', 'WP_C'):
            modus = 'HEIZEN' if cmd == 'WP_H' else 'KUEHLEN'
            atomar_speichern(self.zustandspfad,
                             {'wp_modus': modus, 'wp_heizauftrag_offen': True})
        await self.d.bhkw._senden(cmd)
        if cmd == 'WP_S':
            atomar_speichern(self.zustandspfad,
                             {'wp_modus': 'AUS', 'wp_heizauftrag_offen': False})
            self.wiederanlauf_stop_noetig = False
            self.modus = 'AUS'
            self.an = False
        else:
            self.modus = 'HEIZEN' if cmd == 'WP_H' else 'KUEHLEN'
            self.an = True
        self.letzter_befehl = time.monotonic()
        self._log(art, befehl=cmd, **info)

    async def tick(self):
        aktiv = self.d.automatik.aktiv()
        if self.wiederanlauf_stop_noetig:
            self.letzter_pruefgrund = 'WP_STOP_NACH_NEUSTART_ERFORDERLICH'
            # Nur eine von UNS zuvor aktivierte WP stoppen; manuelle WP unberuehrt.
            if self.d.bhkw._bridge_bereit():
                await self._senden('WP_S', 'WP_STOP_NACH_NEUSTART')
            return
        if not aktiv:
            self.letzter_pruefgrund = 'AUTOMATIK_AUS'
            # Bei AUTO_AUS einmalig einen beauftragten WP-Heizbetrieb beenden.
            if self.an and self.d.bhkw._bridge_bereit():
                await self._senden('WP_S', 'WP_STOP_AUTO_AUS')
            self._vorher_aktiv = False
            return
        self._vorher_aktiv = True
        daten = self._sensoren()
        if daten:
            self._trend(daten['sd'], time.monotonic())
        p = self.d.parameter
        # Uebergangskompatibilitaet fuer alte Tests/Statusobjekte mit .an.
        if self.an and self.modus == 'AUS':
            self.modus = 'HEIZEN'
        motor = self.d.cache.motorstatus(7*60)['zustand']
        # Heizen und Kuehlen sind getrennte Betriebsarten. BHKW sperrt nur
        # KUEHLEN, NICHT das PV-Ueberschussheizen.
        if self.an and self.modus == 'HEIZEN':
            self.letzter_pruefgrund = 'WP_HEIZAUFTRAG_AKTIV'
            grund = None
            if daten is None:
                grund = 'SENSOR_FEHLT'
            elif self.wp_plan_stop is not None and datetime.now(TZ) >= self.wp_plan_stop:
                grund = 'PROGNOSTIZIERTE_PV_ABREGELUNG_BEENDET'
            elif daten['soc'] <= p.wp_soc_aus_prozent:
                grund = 'SOC_UNTER_ABSCHALTGRENZE'
            elif daten['temp'] >= p.maximum_c:
                grund = 'MAXIMALTEMPERATUR'
            elif not (6 <= datetime.now(TZ).hour < 22):
                grund = 'KEINE_PV_TAGESZEIT'
            if grund:
                self.letzter_pruefgrund = grund
                if self.d.bhkw._bridge_bereit():
                    self.wp_heute_beendet = datetime.now(TZ).date().isoformat()
                    await self._senden('WP_S', 'WP_STOP', grund=grund,
                                      soc=daten['soc'] if daten else None)
            return
        if self.an and self.modus == 'KUEHLEN':
            self.letzter_pruefgrund = 'WP_KUEHLAUFTRAG_AKTIV'
            grund = None
            if daten is None:
                grund = 'SENSOR_FEHLT'
            elif motor == 'LAEUFT':
                grund = 'BHKW_LAEUFT_KUEHLSPERRE'
            elif daten['temp'] < p.solltemperatur_c:
                grund = 'SOLLTEMPERATUR_UNTERSCHRITTEN'
            if grund:
                self.letzter_pruefgrund = grund
                if self.d.bhkw._bridge_bereit():
                    await self._senden('WP_S', 'WP_KUEHLUNG_STOP', grund=grund,
                                      temperatur=daten['temp'] if daten else None)
            return
        # Kuehlung hat oberhalb der Maximaltemperatur Vorrang vor der
        # PV-Ueberschussheizung. Temperaturfuehrung ohne Tageszeitgrenze.
        # Ohne bestaetigt ausgeschalteten Motor wird KEINE Kuehlung gestartet.
        if daten and daten['temp'] > p.maximum_c:
            if motor == 'LAEUFT':
                self.letzter_pruefgrund = 'BHKW_LAEUFT_KUEHLSPERRE'
            elif motor in ('UNBEKANNT', 'WIDERSPRUCH'):
                self.letzter_pruefgrund = 'MOTORSTATUS_UNBEKANNT_KUEHLSPERRE'
            elif self.d.bhkw._bridge_bereit():
                self.letzter_pruefgrund = 'TEMP_UEBER_MAXIMUM'
                await self._senden('WP_C', 'WP_KUEHLUNG_START',
                                  temperatur=daten['temp'])
            else:
                self.letzter_pruefgrund = 'BRIDGE_FEHLT'
            return
        if not daten or not self.d.bhkw._bridge_bereit():
            self.letzter_pruefgrund = 'MESSWERTE_ODER_BRIDGE_FEHLEN'
            return
        if not 6 <= datetime.now(TZ).hour < 22:
            self.letzter_pruefgrund = 'AUSSERHALB_PV_TAGESFENSTER'
            return
        if daten['temp'] >= p.maximum_c - max(0.3, p.wp_hysterese_c):
            self.letzter_pruefgrund = 'RAUMTEMPERATUR_ZU_HOCH'
            return
        # Einmalige Einschaltzeitentscheidung je PV-Prognose und Kalendertag.
        # Ohne Festhalten des Zeitpunkts wuerde ein rollierender Optimierer
        # den Start alle 5 Minuten immer weiter in die Zukunft verschieben.
        heute = datetime.now(TZ).date().isoformat()
        if self.wp_heute_beendet == heute:
            self.letzter_pruefgrund = 'WP_HEUTE_BEREITS_BEENDET'
            return
        pv_abgerufen = self.d.pv.state.get('abgerufen')
        if (self.wp_prognose is None or self.wp_prognose_tag != heute
                or self.wp_pv_abgerufen != pv_abgerufen):
            pv_state = self.d.pv.status()
            pv_state['zeitverlauf_wh'] = self.d.pv.state.get('zeitverlauf_wh', {})
            tm = self.d.cache._werte['TM_B'][0]
            inv = self.d.cache._werte['IN_W'][0]
            bm = self.d.cache._werte['BM_W'][0]
            self.wp_prognose = await asyncio.to_thread(
                wp_start_prognose, datetime.now(TZ), pv_state, p,
                tm.innen_c, tm.aussen_c,
                max(0., min(p.batterie1_kwh, p.batterie1_kwh - inv.entnommen_kwh)),
                max(0., min(p.batterie2_kwh, p.batterie2_kwh - bm.entnommen_kwh)))
            self.wp_prognose_zeit = time.monotonic()
            self.wp_prognose_tag = heute
            self.wp_pv_abgerufen = pv_abgerufen
            self._log('WP_PROGNOSE', **self.wp_prognose)
        entscheidung = self.wp_prognose
        self.letzter_pruefgrund = entscheidung['grund']
        start_text = entscheidung.get('einschaltzeit')
        if not start_text:
            return
        start_zeit = datetime.fromisoformat(start_text)
        jetzt_dt = datetime.now(TZ)
        # Vergangene Startfenster nicht spaet nachholen, z.B. nach Dienstpause.
        if jetzt_dt < start_zeit or jetzt_dt > start_zeit + timedelta(minutes=15):
            return
        if daten['soc'] <= p.wp_soc_aus_prozent:
            self.letzter_pruefgrund = 'SOC_UNTER_ABSCHALTGRENZE'
            return
        self.wp_plan_stop = datetime.fromisoformat(entscheidung['ausschaltzeit_prognose'])
        self.letzter_pruefgrund = 'WP_START_PROGNOSE'
        await self._senden('WP_H', 'WP_START_PROGNOSE',
                          soc=daten['soc'], soc_insel=daten['soc_insel'],
                          temperatur=daten['temp'], vorausschau=entscheidung)

    async def loop(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log('WP_FEHLER', fehler=str(exc))
            await asyncio.sleep(30)
