"""Offgrid 0.9.9: schmale BHKW-Kommandoschnittstelle.

Das BHKW selbst entscheidet ueber Startbereitschaft, 53 V, Reinigung und
maximale vier Stunden Laufzeit. Python schickt nur die angeforderten Befehle.
Die Plan-Automatik ist separat, nur lokal und zeitlich begrenzt freigebbar.

Lokale Befehle ueber Unix-Socket: STATUS, FREIGEBEN, START, STOP, SPERREN.
START erfordert eine einmalige, zeitlich beschraenkte Freigabe. STOP nie.
Keine aktiven MO_W-Abfragen waehrend eines laufenden Motors.
"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time


class BHKWSteuerung:
    ARM_DAUER_S = 15 * 60
    FUNKPAUSE_S = 5
    SENDE_WARTE_S = 20
    STATUS_WARN_S = 7 * 60
    STOP_STATUS_TIMEOUT_S = 8
    STOP_STATUS_DELAY_S = 10  # Feste Wartezeit AB dem Senden von MO_AUS

    def __init__(self, dienst):
        self.d = dienst
        self.arm_bis = 0.0
        self.zustand = 'GESPERRT'
        self.erwartung = None
        self.fehler = None
        self.start_gesendet = None
        self.letzter_status_timestamp = None
        self.letzter_mo_empfang = None
        self._stop_status_offen = False
        self._stop_mo_stand = None
        self._stop_pruefung_task = None
        self._stop_pruefung_generation = 0
        self._stop_bestaetigt = None
        self._warnung_ausgegeben = False
        self._lock = asyncio.Lock()
        self._owner_loop = None
        self.ereignispfad = self.d.datenordner / 'bhkw_aktor_ereignisse.jsonl'
        # Keine persistente Start-/Stop-Sperre: Motorzustand kommt von der Hardware.
        self.recovery = None

    def _pruefe_loop(self):
        loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = loop
        elif loop is not self._owner_loop:
            raise RuntimeError('BHKW-Steuerung im falschen Event-Loop')

    def _log(self, art, **details):
        self.d.datenordner.mkdir(parents=True, exist_ok=True)
        entry = dict(zeit_utc=datetime.now(timezone.utc).isoformat(),
                     art=art, zustand=self.zustand, **details)
        with self.ereignispfad.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        return entry

    def _bridge_bereit(self):
        w = self.d.writer
        return bool(self.d.connected and w is not None and not w.is_closing())

    def status(self):
        now = time.monotonic()
        alter = self.d.cache.alter('MO_W')
        motor = self.d.cache.motorstatus(self.STATUS_WARN_S)
        # Nie eine VOR dem Stopp empfangene Laufmeldung als Zustand NACH STOP ausgeben.
        if self._stop_bestaetigt is None and self.zustand == 'STOP_GESENDET':
            motor = {'zustand': 'NACH_STOP_UNBESTAETIGT', 'startbereit': False,
                     'grund': 'Seit MO_AUS noch keine neue MO_W-Rueckmeldung'}
        return {
            'zustand': self.zustand,
            'lokal_freigegeben': self.arm_bis > now,
            'freigabe_rest_s': int(max(0.0, self.arm_bis - now)),
            'automatische_planumsetzung': bool(getattr(getattr(self.d, 'automatik', None), 'aktiv', lambda: False)()),
            'start_utc': self.start_gesendet.isoformat() if self.start_gesendet else None,
            'wartet_auf': self.erwartung,
            'fehler': self.fehler,
            'motor': motor,
            'stop_bestaetigt': self._stop_bestaetigt,
            'motorstatus_alter_s': int(alter) if alter is not None else None,
            'bridge_verbunden': self.d.connected,
            'neustart_sperre': False,
            'hinweis': 'Motorsteuerung prueft Start/Laufzeit/53V selbst; Status nur informativ',
        }

    async def _senden(self, befehl, antwort_name=None):
        """Seriell mit Poller senden, aber nie Status als Startfreigabe verwenden."""
        ende = time.monotonic() + self.SENDE_WARTE_S
        async with self.d.funk_lock:
            while True:
                if not self._bridge_bereit():
                    raise RuntimeError('Bridge nicht verbunden')
                jetzt = time.monotonic()
                if self.d.ausstehend and jetzt - self.d.ausstehend_seit >= self.d.timeout:
                    self.d.ausstehend = None
                pause = max(self.FUNKPAUSE_S, self.d.funkpause)
                rest = pause - (jetzt - self.d.letztes_senden)
                if not self.d.ausstehend and rest <= 0:
                    break
                if jetzt >= ende:
                    raise RuntimeError('Funkverbindung belegt; Befehl nicht gesendet')
                await asyncio.sleep(min(0.2, max(0.05, rest if rest > 0 else 0.1)))
            # Eine gezielte Statusabfrage vor dem Senden als ausstehend
            # kennzeichnen, damit der Hintergrundpoller nicht dazwischenfunkt.
            if antwort_name:
                self.d.ausstehend = antwort_name
                self.d.ausstehend_seit = time.monotonic()
            try:
                self.d.writer.write((befehl + '\n').encode('ascii'))
                await self.d.writer.drain()
            except Exception:
                if antwort_name and self.d.ausstehend == antwort_name:
                    self.d.ausstehend = None
                raise
            self.d.letztes_senden = time.monotonic()
            self._log('STATUSABFRAGE_GESENDET' if antwort_name else
                      'AKTORBEFEHL_ABGESENDET', befehl=befehl)

    async def _status_nach_stop(self, generation):
        """Einmalige MO_W-Nachkontrolle fruehestens 10 s nach MO_AUS.

        Die Wartezeit beginnt erst NACH dem abgeschlossenen Sendevorgang
        von MO_AUS; die allgemeine Funkpause kommt bei Bedarf hinzu.
        Passiv eintreffende Motorantworten waehrend der Wartezeit duerfen
        diese gezielte Nachkontrolle nicht vorzeitig abbrechen.
        """
        try:
            await asyncio.sleep(self.STOP_STATUS_DELAY_S)
            if generation != self._stop_pruefung_generation:
                return
            # Zwischen STOP und jetzt kann bereits eine passive MO_W-Meldung
            # gekommen sein. Fuer die Nachkontrolle muss die Antwort aber
            # juenger als die JETZT gestartete gezielte Abfrage sein.
            vor = (self.d.cache._werte['MO_W'][2]
                   if 'MO_W' in self.d.cache._werte else None)
            self._stop_mo_stand = vor
            self._stop_status_offen = True
            await self._senden('MO_W', antwort_name='MO_W')
            ende = time.monotonic() + self.STOP_STATUS_TIMEOUT_S
            while time.monotonic() < ende:
                if generation != self._stop_pruefung_generation:
                    return
                entry = self.d.cache._werte.get('MO_W')
                if entry is not None and entry[2] != vor:
                    return  # motor_neu hat den neuen Status uebernommen.
                await asyncio.sleep(.1)
            if generation == self._stop_pruefung_generation:
                self._stop_status_offen = False
                self.fehler = 'Keine neue MO_W-Antwort auf Nachkontrolle 10 s nach STOP'
                self._log('STOP_STATUS_TIMEOUT')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation == self._stop_pruefung_generation:
                self._stop_status_offen = False
                self.fehler = 'MO_W-Nachkontrolle fehlgeschlagen: ' + str(exc)
                self._log('STOP_STATUSABFRAGE_FEHLER', fehler=str(exc))

    async def lokal(self, aktion):
        self._pruefe_loop()
        async with self._lock:
            now = time.monotonic()
            if aktion == 'STATUS':
                return self.status()
            if aktion == 'SPERREN':
                self.arm_bis = 0.0
                self._log('LOKAL_GESPERRT')
                return self.status()
            if aktion == 'BESTAETIGEN':
                # Rueckwaertskompatibel: keine Neustartsperre mehr vorhanden.
                self._log('BESTAETIGEN_NICHT_ERFORDERLICH')
                return self.status()
            if aktion == 'FREIGEBEN':
                if not self._bridge_bereit():
                    raise RuntimeError('Bridge nicht verbunden')
                self.arm_bis = now + self.ARM_DAUER_S
                self._log('LOKAL_FREIGEGEBEN', dauer_s=self.ARM_DAUER_S)
                return self.status()
            if aktion == 'START':
                if now >= self.arm_bis:
                    raise RuntimeError('Lokale Startfreigabe fehlt/abgelaufen')
                # Bekannter laufender Motor: kein zweiter Startbefehl.
                motor = self.d.cache.motorstatus(self.STATUS_WARN_S)
                if motor['zustand'] == 'LAEUFT' or self.zustand in ('START_GESENDET', 'LAEUFT'):
                    raise RuntimeError('Motor laeuft bereits bzw. Start wurde gesendet')
                # Freigabe vor dem Senden verbrauchen: niemals selbsttätig retry.
                self.arm_bis = 0.0
                self.zustand = 'START_GESENDET'
                self.erwartung = 'LAEUFT'
                try:
                    await self._senden('MO_AN')
                except Exception as exc:
                    self.zustand = 'UNGEWISS'
                    self.fehler = 'MO_AN nicht sicher zugestellt: ' + str(exc)
                    self._log('START_VERSAND_UNGEWISS', fehler=str(exc))
                    raise
                self.start_gesendet = datetime.now(timezone.utc)
                self.fehler = None
                self._log('START_AUFTRAG_ERTEILT')
                return self.status()
            if aktion == 'STOP':
                # Stop niemals von einer frischen MO_W-Antwort oder Neustartsperre
                # abhaengig machen. Das BHKW ignoriert unpassende MO_AUS selbst.
                self.arm_bis = 0.0
                self.zustand = 'STOP_GESENDET'
                self.erwartung = 'AUS'
                self._stop_status_offen = False
                self._stop_bestaetigt = None
                self._stop_pruefung_generation += 1
                generation = self._stop_pruefung_generation
                if self._stop_pruefung_task and not self._stop_pruefung_task.done():
                    self._stop_pruefung_task.cancel()
                try:
                    await self._senden('MO_AUS')
                except Exception as exc:
                    self.zustand = 'UNGEWISS'
                    self.fehler = 'MO_AUS nicht sicher zugestellt: ' + str(exc)
                    self._log('STOP_VERSAND_UNGEWISS', fehler=str(exc))
                    raise
                self.fehler = None
                self._stop_mo_stand = (self.d.cache._werte['MO_W'][2]
                                       if 'MO_W' in self.d.cache._werte else None)
                self._stop_status_offen = True
                self._log('STOP_AUFTRAG_ERTEILT')
                self._stop_pruefung_task = asyncio.create_task(self._status_nach_stop(generation))
                return self.status()
            raise ValueError('Unbekannte Aktion')

    async def motor_neu(self):
        """Passiv empfangenen MO_W auswerten, nie selbst Funk senden."""
        self._pruefe_loop()
        entry = self.d.cache._werte.get('MO_W')
        if entry is None or entry[2] == self.letzter_status_timestamp:
            return
        self.letzter_status_timestamp = entry[2]
        if self._stop_status_offen and entry[2] != self._stop_mo_stand:
            self._stop_status_offen = False
            motor_post = self.d.cache.motorstatus(self.STATUS_WARN_S)
            if motor_post['zustand'] == 'LAEUFT':
                self._stop_bestaetigt = False
                self.fehler = 'Motor meldet nach MO_AUS weiterhin Drehzahl > 0'
                self._log('STOP_NICHT_BESTAETIGT_MOTOR_LAEUFT')
            elif motor_post['zustand'] in ('AUS_BEREIT', 'REINIGUNG', 'AUS_ZU_HEISS'):
                self._stop_bestaetigt = True
                self.fehler = None
                self._log('STOP_BESTAETIGT')
        self.letzter_mo_empfang = time.monotonic()
        self._warnung_ausgegeben = False
        # Ein ausstehender/fehlgeschlagener Stopp darf nicht durch irgendeine
        # allgemeine Motorstatus-Meldung stillschweigend als Erfolg gelten.
        motor = self.d.cache.motorstatus(self.STATUS_WARN_S)
        alt = self.zustand
        if motor['zustand'] == 'LAEUFT':
            self.zustand = 'LAEUFT_NACH_STOP' if self._stop_bestaetigt is False else 'LAEUFT'
            self.erwartung = None
        elif motor['zustand'] in ('AUS_BEREIT', 'REINIGUNG', 'AUS_ZU_HEISS'):
            self.zustand = 'GESPERRT'
            self.erwartung = None
            self.arm_bis = 0.0 if alt in ('LAEUFT', 'STOP_GESENDET') else self.arm_bis
        # UNBEKANNT und WIDERSPRUCH nicht zu Kommandos oder Sperren hochstufen.
        if self.zustand != alt:
            self._log('MOTORZUSTAND', motor=motor['zustand'], vorher=alt)

    async def ueberwachen(self):
        """Nur informieren; Motor beendet sich selbst und meldet alle 5 min."""
        self._pruefe_loop()
        while True:
            await asyncio.sleep(10)
            if self.zustand not in ('LAEUFT', 'START_GESENDET', 'STOP_GESENDET', 'LAEUFT_NACH_STOP'):
                continue
            alter = self.d.cache.alter('MO_W')
            # Bei fehlendem Status den Zustand nicht endlos als 'LAEUFT'
            # festhalten. Das ist nur ein informativer Zustand, keine Sperre.
            start_zu_alt = (self.zustand == 'START_GESENDET' and
                            self.start_gesendet is not None and
                            (datetime.now(timezone.utc) - self.start_gesendet).total_seconds() > self.STATUS_WARN_S)
            if (alter is None or alter > self.STATUS_WARN_S or start_zu_alt) and not self._warnung_ausgegeben:
                self._warnung_ausgegeben = True
                self.fehler = 'Motorstatus seit mehr als 7 Minuten nicht bestaetigt'
                self.zustand = 'STATUS_UNBEKANNT'
                self.erwartung = None
                self._log('MOTORSTATUS_FEHLT', alter_s=alter)
            elif alter is not None and alter <= self.STATUS_WARN_S:
                self._warnung_ausgegeben = False
                if self._stop_bestaetigt is not False:
                    self.fehler = None
