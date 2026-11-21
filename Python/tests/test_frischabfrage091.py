"""v0.9.5: BHKW-Hardware uebernimmt die frueheren Startpruefungen."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from sensor_cache import SensorCache
from bhkw_steuerung import BHKWSteuerung

OFF = 'AAUS 0 min 0 rpm Tk 35.0 Tp 25.0 0 W 0 % h: 7900 0%'
ON = 'AAN 1 min 2500 rpm Tk 60.0 Tp 45.0 750 W 0 % h: 7900 18%'
BM = 'Ch: 142.5 Ah En: 3.00 kWh Pd: 200 W Pa: 0 W Ia: 1.1 A U: 50.0 V T: 28.8 C'

class Writer:
    def __init__(self): self.sent = []
    def is_closing(self): return False
    def write(self, data): self.sent.append(data)
    async def drain(self): pass

class SimpleBHKW(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = SimpleNamespace(datenordner=Path(self.tmp.name), cache=SensorCache(),
                connected=True, writer=Writer(), ausstehend=None, ausstehend_seit=0,
                timeout=1, letztes_senden=float('-inf'), funkpause=0,
                funk_lock=asyncio.Lock())
        self.c = BHKWSteuerung(self.d)
        self.c.FUNKPAUSE_S = 0

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_one_start_per_freigabe_without_sensor_poll(self):
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])
        with self.assertRaises(RuntimeError): await self.c.lokal('START')

    async def test_running_motor_prevents_repeated_start(self):
        self.d.cache.aufnehmen(ON)
        await self.c.lokal('FREIGEBEN')
        with self.assertRaisesRegex(RuntimeError, 'Motor laeuft'):
            await self.c.lokal('START')
        self.assertFalse(self.d.writer.sent)

    async def test_missing_mo_and_bm_do_not_block_hardware_start(self):
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])

    async def test_full_battery_is_handled_by_bhkw(self):
        self.d.cache.aufnehmen(BM.replace('50.0 V','53.0 V'))
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])

    async def test_no_automatic_actuation(self):
        self.assertFalse(self.c.status()['automatische_planumsetzung'])
        self.assertFalse(self.d.writer.sent)
