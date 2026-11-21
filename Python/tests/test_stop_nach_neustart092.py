"""v0.9.5: Stop jederzeit lokal moeglich, ohne alten Wiederanlaufzustand."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from sensor_cache import SensorCache
from bhkw_steuerung import BHKWSteuerung

ON = 'AAN 1 min 2500 rpm Tk 60.0 Tp 45.0 750 W 0 % h: 7900 18%'
class Writer:
    def __init__(self): self.sent=[]
    def is_closing(self): return False
    def write(self, data): self.sent.append(data)
    async def drain(self): pass

class StopAfterReboot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.d=SimpleNamespace(datenordner=Path(self.tmp.name), cache=SensorCache(),
            connected=True, writer=Writer(), ausstehend=None, ausstehend_seit=0,
            timeout=1, letztes_senden=float('-inf'), funkpause=0, funk_lock=asyncio.Lock())
        self.c=BHKWSteuerung(self.d)
        self.c.FUNKPAUSE_S=0

    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_stop_after_restart_without_fresh_sensor(self):
        # vorherige Zustandsdatei hat fuer die neue Steuerung keine Sperrwirkung
        (self.d.datenordner/'bhkw_aktor_zustand.json').write_text('{"lauf_ungeklaert":true}')
        c2=BHKWSteuerung(self.d)
        c2.FUNKPAUSE_S=0
        await c2.lokal('STOP')
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n'])
        self.assertIsNone(c2.fehler)

    async def test_stop_even_without_mo(self):
        await self.c.lokal('STOP')
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n'])

    async def test_motor_status_is_passive(self):
        self.d.cache.aufnehmen(ON)
        await self.c.motor_neu()
        self.assertEqual(self.c.zustand,'LAEUFT')
        self.assertEqual(self.d.writer.sent,[])

    async def test_no_four_hour_python_stop(self):
        self.d.cache.aufnehmen(ON)
        await self.c.motor_neu()
        task=asyncio.create_task(self.c.ueberwachen())
        try:
            await asyncio.sleep(0.05)
            self.assertFalse(self.d.writer.sent)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
