import asyncio
from datetime import datetime
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo
from bhkw_automatik import BHKWAutomatik

TZ=ZoneInfo('Europe/Berlin')

class Motor:
    STATUS_WARN_S=420
    def __init__(self):
        self._lock=asyncio.Lock()
        self.zustand='GESPERRT'
        self.sent=[]
        self.start_gesendet=None
        self.erwartung=None
    def _bridge_bereit(self): return True
    def _log(self, *a, **kw): pass
    async def _senden(self, command): self.sent.append(command)

class TestAuto101(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.d=Mock()
        self.d.datenordner=Path(self.t.name)
        self.d.bhkw=Motor()
        self.d.manuell.aktiv.return_value=False
        self.d.cache.motorstatus.return_value={'zustand':'AUS_BEREIT'}
        self.auto=BHKWAutomatik(self.d)
        self.auto.ein()
    async def asyncTearDown(self):
        self.auto.aus()
        await asyncio.sleep(0)
        self.t.cleanup()
    def events(self):
        return [json.loads(line) for line in self.auto.entscheidungslog.read_text().splitlines()]
    async def test_early_preplan_and_single_start(self):
        async def plan():
            return {'automatische_auswahl': {'anzahl':4,'zulaessig':True, 'startzeiten_im_horizont':['2026-11-20T04:00:00+01:00']}}
        self.d.plan_ohne_blockieren=plan
        await self.auto.tick(datetime(2026,11,20,3,50,5,tzinfo=TZ))
        await asyncio.sleep(0)
        await self.auto.tick(datetime(2026,11,20,4,0,1,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,9,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent,['MO_AN'])
        self.assertEqual(sum(e['art']=='AUTO_START_VERSUCH' for e in self.events()),1)
    async def test_plan_with_no_start_is_logged(self):
        async def plan():
            return {'automatische_auswahl': {'anzahl':3,'zulaessig':True, 'startzeiten_im_horizont':['2026-11-20T12:00:00+01:00']}}
        self.d.plan_ohne_blockieren=plan
        await self.auto.tick(datetime(2026,11,20,3,50,5,tzinfo=TZ))
        await asyncio.sleep(0)
        await self.auto.tick(datetime(2026,11,20,4,0,2,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent,[])
        self.assertTrue(any(e['art']=='AUTO_START_AUSGELASSEN' and e['grund']=='KEINE_FREIGEGEBENE_PLANENTSCHEIDUNG' for e in self.events()))
    async def test_slow_plan_does_not_block_tick(self):
        started=asyncio.Event()
        gate=asyncio.Event()
        async def slow():
            started.set()
            await gate.wait()
            return {'automatische_auswahl': {'anzahl':4,'zulaessig':True,'startzeiten_im_horizont':['2026-11-20T04:00:00+01:00']}}
        self.d.plan_ohne_blockieren=slow
        await asyncio.wait_for(self.auto.tick(datetime(2026,11,20,3,50,5,tzinfo=TZ)), .5)
        await started.wait()
        await asyncio.wait_for(self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ)), .5)
        self.assertEqual(self.d.bhkw.sent,[])
        gate.set()
        await asyncio.sleep(0)
    async def test_auto_ein_creates_decision_file(self):
        self.assertTrue(self.auto.entscheidungslog.exists())
        self.assertEqual(self.events()[0]['art'],'AUTO_AKTIVIERT')
    async def test_running_motor_prevents_start_with_reason(self):
        async def plan():
            return {'automatische_auswahl': {'anzahl':4,'zulaessig':True,'startzeiten_im_horizont':['2026-11-20T04:00:00+01:00']}}
        self.d.plan_ohne_blockieren=plan
        self.d.cache.motorstatus.return_value={'zustand':'LAEUFT'}
        await self.auto.tick(datetime(2026,11,20,3,50,5,tzinfo=TZ))
        await asyncio.sleep(0)
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        self.assertFalse(self.d.bhkw.sent)
        self.assertTrue(any(e.get('grund')=='MOTOR_LAEUFT' for e in self.events()))
