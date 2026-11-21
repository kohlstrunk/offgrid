"""Beaufsichtigte BHKW-Planumsetzung, ohne Einfluss auf Hardware-Selbstschutz.

Nur lokale Aktivierung; ohne Zeitlimit, nicht persistent. Anlaufversuche werden
vor dem Senden dauerhaft notiert: kein doppeltes MO_AN nach Neustart/Timeout.
"""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from parameter import STARTPLAENE

TZ = ZoneInfo('Europe/Berlin')


class BHKWAutomatik:
    STARTFENSTER_S = 90
    VORPLANUNG_S = 10 * 60

    def __init__(self, dienst):
        self.d = dienst
        self.freigegeben = False
        self.ledger_path = Path(dienst.datenordner) / 'bhkw_auto_startversuche.json'
        self.versuche = self._lesen()
        self.letztes_ereignis = None
        self.vorfreigaben = {}  # slot -> Zeitpunkt der gueltigen Planentscheidung
        self.vorplanung_tasks = {}  # slot -> Hintergrundberechnung
        self.behandelte_slots = set()  # pro Prozess keine mehrfachen Ablehnungen
        self.entscheidungslog = Path(dienst.datenordner) / 'bhkw_auto_entscheidungen.jsonl'

    def _ereignis(self, art, **details):
        """Auch negative Entscheidungen schreiben; ohne Funk oder Motorbefehl."""
        self.entscheidungslog.parent.mkdir(parents=True, exist_ok=True)
        with self.entscheidungslog.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(zeit_utc=datetime.now(TZ).astimezone(
                ZoneInfo('UTC')).isoformat(), art=art, **details),
                ensure_ascii=False, default=str) + '\n')
        self.letztes_ereignis = art + (' ' + str(details.get('slot', '')) if details.get('slot') else '')

    def _lesen(self):
        try:
            data = json.loads(self.ledger_path.read_text(encoding='utf-8'))
            if not isinstance(data, list) or any(not isinstance(s, str) for s in data):
                raise ValueError('Ungueltiges Startprotokoll')
            return set(data)
        except FileNotFoundError:
            return set()
        # Andere Fehler absichtlich nicht verschlucken: ohne Protokoll kein Start.

    def _speichern(self):
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.ledger_path.with_name(self.ledger_path.name + '.tmp')
        temp.write_text(json.dumps(sorted(self.versuche), indent=2), encoding='utf-8')
        temp.replace(self.ledger_path)

    def aktiv(self):
        return self.freigegeben

    def status(self):
        return {'aktiv': self.aktiv(),
                'freigabe_rest_s': None,
                'automatisch_nach_neustart': False,
                'letztes_ereignis': self.letztes_ereignis,
                'startversuche_protokolliert': len(self.versuche),
                'entscheidungsprotokoll': str(self.entscheidungslog),
                'vorgeplante_slots': sorted(self.vorfreigaben)}

    def ein(self):
        if not self.d.bhkw._bridge_bereit():
            raise RuntimeError('Bridge nicht verbunden')
        if self.d.manuell.aktiv():
            raise RuntimeError('Manuelles RE_P-Schema aktiv; zuerst RE_PA')
        self.freigegeben = True
        self.vorfreigaben.clear()
        self.behandelte_slots.clear()
        for task in self.vorplanung_tasks.values():
            task.cancel()
        self.vorplanung_tasks.clear()
        self.letztes_ereignis = 'LOKAL_DAUERHAFT_AKTIVIERT'
        self.d.bhkw._log('AUTOMATIK_AKTIVIERT', dauer='ohne_zeitlimit')
        self._ereignis('AUTO_AKTIVIERT', dauer='ohne_zeitlimit')
        return self.status()

    def aus(self):
        self.freigegeben = False
        self.vorfreigaben.clear()
        for task in self.vorplanung_tasks.values():
            task.cancel()
        self.vorplanung_tasks.clear()
        self.letztes_ereignis = 'LOKAL_DEAKTIVIERT'
        self.d.bhkw._log('AUTOMATIK_DEAKTIVIERT')
        self._ereignis('AUTO_DEAKTIVIERT')
        return self.status()

    @staticmethod
    def kandidaten(schema, jetzt):
        """Nur kurz nach einem echten Startslot, nie Starts nachholen."""
        if schema not in STARTPLAENE:
            return []
        midnight = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
        result = []
        for slot in STARTPLAENE[schema]:
            start = midnight + timedelta(hours=slot)
            delay = (jetzt - start).total_seconds()
            if 0 <= delay < BHKWAutomatik.STARTFENSTER_S:
                result.append((start.isoformat(timespec='minutes'), start))
        return result

    @staticmethod
    def naechste_slots(jetzt):
        midnight = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
        starts = set()
        for slots in STARTPLAENE.values():
            for hour in slots:
                start = midnight + timedelta(hours=hour)
                rest = (start - jetzt).total_seconds()
                if 0 < rest <= BHKWAutomatik.VORPLANUNG_S:
                    starts.add(start)
        return sorted(starts)

    async def _vorplanen(self, slot):
        key = slot.isoformat(timespec='minutes')
        try:
            plan = await self.d.plan_ohne_blockieren()
            auswahl = plan.get('automatische_auswahl') or {}
            nummer = auswahl.get('anzahl')
            startbar = bool(auswahl.get('zulaessig') or auswahl.get('automatisch_startbar'))
            enthalten = any(datetime.fromisoformat(s) == slot
                            for s in auswahl.get('startzeiten_im_horizont', []))
            if self.aktiv() and startbar and enthalten:
                self.vorfreigaben[key] = time.monotonic()
                self._ereignis('AUTO_VORPLANUNG_START_FREIGEGEBEN', slot=key,
                               schema=nummer)
            else:
                self._ereignis('AUTO_VORPLANUNG_KEIN_START', slot=key,
                               schema=nummer, startbar=startbar,
                               enthalten=enthalten, gruende=auswahl.get('gruende', []))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ereignis('AUTO_VORPLANUNG_FEHLER', slot=key, fehler=str(exc))
        finally:
            self.vorplanung_tasks.pop(key, None)

    async def tick(self, jetzt=None):
        if not self.aktiv():
            return
        jetzt = jetzt or datetime.now(TZ)
        if self.d.manuell.aktiv():
            self.aus()
            self._ereignis('AUTO_AUS_MANUELLES_SCHEMA')
            return
        if not self.d.bhkw._bridge_bereit():
            # Bridgeausfall protokollieren, falls eine Startzeit betroffen ist.
            bridge_bereit = False
        else:
            bridge_bereit = True

        # Planung schon 10 Minuten vor dem Start und als eigene Task. Der
        # 5-s-Tick darf nicht an einer langsamen 24-h-Simulation haengen.
        for slot in self.naechste_slots(jetzt):
            key = slot.isoformat(timespec='minutes')
            if (key not in self.vorplanung_tasks and
                    key not in self.vorfreigaben and
                    key not in self.behandelte_slots and
                    key not in self.versuche):
                # Nur EINE Vorplanung je Slot, auch wenn ihr Ergebnis keinen Start vorsieht.
                self.behandelte_slots.add(key)
                self._ereignis('AUTO_VORPLANUNG_BEGONNEN', slot=key)
                self.vorplanung_tasks[key] = asyncio.create_task(self._vorplanen(slot))
        # Fertige Kurzberechnungen sofort übernehmen; langsame blockieren den Tick nicht.
        if self.vorplanung_tasks:
            await asyncio.sleep(0)

        midnight = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
        for hour in sorted({h for hours in STARTPLAENE.values() for h in hours}):
            slot = midnight + timedelta(hours=hour)
            delay = (jetzt - slot).total_seconds()
            if not 0 <= delay < self.STARTFENSTER_S:
                continue
            key = slot.isoformat(timespec='minutes')
            # Fuer diesen Zeitpunkt genau eine Auswertung protokollieren.
            event_key = 'SLOT:' + key
            if event_key in self.behandelte_slots:
                continue
            if key in self.versuche:
                self.behandelte_slots.add(event_key)
                self._ereignis('AUTO_SLOT_BEREITS_VERSUCHT', slot=key)
                continue
            task = self.vorplanung_tasks.get(key)
            if task is not None and not task.done():
                # Kurzzeitig auf Ergebnis warten, ohne den 5s-Tick zu blockieren.
                # Bis Ablauf des Startfensters bleibt der Slot offen.
                if delay < self.STARTFENSTER_S - 6:
                    continue
                grund = 'VORPLANUNG_NOCH_NICHT_FERTIG'
            elif key not in self.vorfreigaben:
                grund = 'KEINE_FREIGEGEBENE_PLANENTSCHEIDUNG'
            elif not bridge_bereit:
                grund = 'BRIDGE_NICHT_VERBUNDEN'
            elif time.monotonic() - self.vorfreigaben[key] > self.VORPLANUNG_S + 120:
                grund = 'VORPLANUNG_ZU_ALT'
            else:
                grund = None
            if grund:
                self.behandelte_slots.add(event_key)
                self._ereignis('AUTO_START_AUSGELASSEN', slot=key, grund=grund)
                continue
            try:
                async with self.d.bhkw._lock:
                    if not self.aktiv():
                        return
                    if key in self.versuche:
                        continue
                    motor = self.d.cache.motorstatus(self.d.bhkw.STATUS_WARN_S)
                    if motor['zustand'] == 'LAEUFT' or self.d.bhkw.zustand in ('LAEUFT', 'START_GESENDET'):
                        self.behandelte_slots.add(event_key)
                        self._ereignis('AUTO_START_AUSGELASSEN', slot=key,
                                       grund='MOTOR_LAEUFT')
                        continue
                    self.versuche.add(key)
                    try:
                        self._speichern()
                    except Exception:
                        self.versuche.discard(key)
                        raise
                    self.behandelte_slots.add(event_key)
                    self._ereignis('AUTO_START_VERSUCH', slot=key)
                    self.d.bhkw._log('AUTO_START_VERSUCH', slot=key)
                    try:
                        await self.d.bhkw._senden('MO_AN')
                        self.d.bhkw.zustand = 'START_GESENDET'
                        self.d.bhkw.start_gesendet = datetime.now(TZ)
                        self.d.bhkw.erwartung = 'LAEUFT'
                        self._ereignis('AUTO_START_GESENDET', slot=key)
                        self.d.bhkw._log('AUTO_START_GESENDET', slot=key)
                    except Exception as exc:
                        self._ereignis('AUTO_START_UNGEWISS', slot=key, fehler=str(exc))
                        self.d.bhkw._log('AUTO_START_UNGEWISS', slot=key, fehler=str(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.behandelte_slots.add(event_key)
                self._ereignis('AUTO_STARTFEHLER', slot=key, fehler=str(exc))
                self.aus()
                return

    async def loop(self):
        while True:
            if self.aktiv():
                await self.tick()
            await asyncio.sleep(5)
