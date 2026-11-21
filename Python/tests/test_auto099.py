import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from bhkw_automatik import BHKWAutomatik

TZ = ZoneInfo('Europe/Berlin')

class DummyMotor:
    STATUS_WARN_S = 420
    def __init__(self):
        self._lock = asyncio.Lock()
        self.zustand = 'GESPERRT'
        self.sent = []
        self.start_gesendet = None
        self.erwartung = None
    def _bridge_bereit(self):
        return True
    def _log(self, *a, **kw):
        return None
    async def _senden(self, cmd):
        self.sent.append(cmd)

class TestAuto(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Mock()
        self.d.datenordner = Path(self.tmp.name)
        self.d.bhkw = DummyMotor()
        self.d.manuell.aktiv.return_value = None
        self.d.cache.motorstatus.return_value = {'zustand':'AUS_BEREIT'}
        self.auto = BHKWAutomatik(self.d)
        self.auto.ein()
        self.d.plan_ohne_blockieren = self.make_plan

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def make_plan(self):
        return {'automatische_auswahl': {'anzahl': 3, 'zulaessig': True,
                'startzeiten_im_horizont':['2026-11-20T04:00:00+01:00',
                                              '2026-11-20T12:00:00+01:00',
                                              '2026-11-20T17:00:00+01:00']}}

    async def test_preflight_then_start_once(self):
        await self.auto.tick(datetime(2026,11,20,3,59,30,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,15,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, ['MO_AN'])
        self.assertIn('2026-11-20T04:00+01:00', self.auto.versuche)

    async def test_never_start_without_preflight(self):
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, [])

    async def test_reload_disarmed_persisted(self):
        await self.auto.tick(datetime(2026,11,20,3,59,30,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        newer = BHKWAutomatik(self.d)
        self.assertFalse(newer.aktiv())
        self.assertIn('2026-11-20T04:00+01:00', newer.versuche)

    async def test_manual_stop_clears_approval(self):
        self.auto.aus()
        await self.auto.tick(datetime(2026,11,20,3,59,30,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, [])

    async def test_late_slot_not_started(self):
        await self.auto.tick(datetime(2026,11,20,3,59,30,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,2,1,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, [])

    async def test_heizdefizit_fallback_can_start(self):
        async def fallback():
            return {'automatische_auswahl': {'anzahl': 5,
                'zulaessig': False, 'automatisch_startbar': True,
                'heizdefizit_notplan': True,
                'startzeiten_im_horizont': ['2026-11-20T19:00:00+01:00']}}
        self.d.plan_ohne_blockieren = fallback
        await self.auto.tick(datetime(2026, 11, 20, 18, 59, 30, tzinfo=TZ))
        await self.auto.tick(datetime(2026, 11, 20, 19, 0, 5, tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, ['MO_AN'])

    async def test_unavailable_plan_no_start(self):
        async def nope():
            return {'automatische_auswahl': {'anzahl':3,'zulaessig':False}}
        self.d.plan_ohne_blockieren = nope
        await self.auto.tick(datetime(2026,11,20,3,59,30,tzinfo=TZ))
        await self.auto.tick(datetime(2026,11,20,4,0,5,tzinfo=TZ))
        self.assertEqual(self.d.bhkw.sent, [])

if __name__ == '__main__':
    unittest.main()

