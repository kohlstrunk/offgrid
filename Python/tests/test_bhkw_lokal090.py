import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
from sensor_cache import SensorCache
from sensoren import parse_mo_w, parse_bm_w
from bhkw_steuerung import BHKWSteuerung

OFF = 'AAUS 0 min 0 rpm Tk 35.0 Tp 25.0 0 W 0 % h: 7900 0%'
ON = 'AAN 1 min 2500 rpm Tk 60.0 Tp 45.0 750 W 0 % h: 7900 18%'
BM = 'Ch: 142.5 Ah En: 3.00 kWh Pd: 200 W Pa: 0 W Ia: 1.1 A U: 50.0 V T: 28.8 C'

class Writer:
    def __init__(self): self.sent=[]
    def is_closing(self): return False
    def write(self, data): self.sent.append(data)
    async def drain(self): pass

class LocalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = SimpleNamespace(datenordner=Path(self.tmp.name), cache=SensorCache(),
                            connected=True, writer=Writer(), ausstehend=None,
                            letztes_senden=float('-inf'), funkpause=0, timeout=1,
                            ausstehend_seit=0, funk_lock=asyncio.Lock())
        d.cache.aufnehmen(OFF)
        d.cache.aufnehmen(BM)
        self.d=d
        self.c=BHKWSteuerung(d)

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_start_einmalig(self):
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])
        with self.assertRaises(RuntimeError): await self.c.lokal('START')
        self.assertFalse(self.c.status()['lokal_freigegeben'])

    async def test_start_nicht_ohne_freigabe(self):
        with self.assertRaises(RuntimeError): await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [])

    async def test_bestaetigung_und_stop(self):
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.d.cache.aufnehmen(ON)
        await self.c.motor_neu()
        self.assertEqual(self.c.zustand, 'LAEUFT')
        self.d.letztes_senden=float('-inf')
        await self.c.lokal('STOP')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n',b'MO_AUS\n'])
        # STOP darf auf Wunsch erneut gesendet werden; BHKW ignoriert unpassende Befehle.
        self.assertFalse(self.c.status()['lokal_freigegeben'])
        self.d.cache.aufnehmen(OFF)
        await self.c.motor_neu()
        self.assertEqual(self.c.zustand,'GESPERRT')
        self.assertIsNone(self.c.recovery)

    async def test_neustart_keine_freigabe(self):
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        c2=BHKWSteuerung(self.d)
        self.assertEqual(c2.zustand, 'GESPERRT')
        with self.assertRaises(RuntimeError): await c2.lokal('START')
        await c2.lokal('BESTAETIGEN')
        self.assertIsNone(c2.recovery)
        self.assertFalse(c2.status()['lokal_freigegeben'])

    async def test_ungeeigneter_status(self):
        self.d.cache.aufnehmen('AMAUS 0 min 0 rpm Tk 35.0 Tp 25.0 0 W 0 % h: 7900 0%')
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])

    async def test_53_volt_sperre(self):
        self.d.cache.aufnehmen(BM.replace('50.0 V', '53.0 V'))
        await self.c.lokal('FREIGEBEN')
        await self.c.lokal('START')
        self.assertEqual(self.d.writer.sent, [b'MO_AN\n'])

    async def test_automatik_ist_nicht_aktiv(self):
        self.assertFalse(self.c.status()['automatische_planumsetzung'])
        self.assertEqual(self.d.writer.sent, [])
