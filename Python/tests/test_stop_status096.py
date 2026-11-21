import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from bhkw_steuerung import BHKWSteuerung
from sensor_cache import SensorCache

OFF = 'AAUS 0 min 0 rpm Tk 35.0 Tp 25.0 0 W 0 % h: 7900 0%'
ON = 'AAN 1 min 2500 rpm Tk 60.0 Tp 45.0 750 W 0 % h: 7900 18%'

class Writer:
    def __init__(self):
        self.sent=[]
        self.on_send=None
    def is_closing(self): return False
    def write(self, data):
        self.sent.append(data)
        if self.on_send: self.on_send(data)
    async def drain(self): pass

class StopStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.d=SimpleNamespace(datenordner=Path(self.tmp.name), cache=SensorCache(),
            connected=True, writer=Writer(), ausstehend=None, ausstehend_seit=0,
            letztes_senden=float('-inf'), funkpause=0, timeout=1,
            funk_lock=asyncio.Lock())
        self.d.cache.aufnehmen(ON)
        self.c=BHKWSteuerung(self.d)
        self.c.FUNKPAUSE_S=0
        self.c.STOP_STATUS_TIMEOUT_S=.25
        self.c.STOP_STATUS_DELAY_S=.03
    async def asyncTearDown(self): self.tmp.cleanup()

    async def test_stop_triggers_query_without_status_and_confirms_off(self):
        async def answer():
            await asyncio.sleep(.02)
            self.d.cache.aufnehmen(OFF)
            await self.c.motor_neu()
            self.d.ausstehend=None
        def on_send(cmd):
            if cmd == b'MO_W\n': asyncio.create_task(answer())
        self.d.writer.on_send=on_send
        await self.c.lokal('STOP')
        self.assertEqual((await self.c.lokal('STATUS'))['motor']['zustand'], 'NACH_STOP_UNBESTAETIGT')
        await asyncio.sleep(.15)
        self.assertEqual(self.d.writer.sent,[b'MO_AUS\n', b'MO_W\n'])
        s=await self.c.lokal('STATUS')
        self.assertEqual(s['motor']['zustand'], 'AUS_BEREIT')
        self.assertIs(s['stop_bestaetigt'],True)
        self.assertEqual(s['zustand'],'GESPERRT')

    async def test_query_not_sent_before_delay(self):
        self.c.STOP_STATUS_DELAY_S=.12
        await self.c.lokal('STOP')
        await asyncio.sleep(.06)
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n'])
        await asyncio.sleep(.09)
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n', b'MO_W\n'])

    async def test_passive_status_does_not_suppress_delayed_check(self):
        self.c.STOP_STATUS_DELAY_S=.08
        await self.c.lokal('STOP')
        # Kurz nach STOP: weiterhin 1 rpm; spaeter muss MO_W erneut kommen.
        interim='AAN 1 min 1 rpm Tk 45.0 Tp 35.0 0 W 0 % h: 7900 0%'
        self.d.cache.aufnehmen(interim)
        await self.c.motor_neu()
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n'])
        async def answer():
            await asyncio.sleep(.01)
            self.d.cache.aufnehmen(OFF)
            await self.c.motor_neu()
            self.d.ausstehend=None
        self.d.writer.on_send=lambda cmd: asyncio.create_task(answer()) if cmd == b'MO_W\n' else None
        await asyncio.sleep(.17)
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n', b'MO_W\n'])
        self.assertIs(self.c.status()['stop_bestaetigt'], True)

    async def test_passive_status_after_stop(self):
        await self.c.lokal('STOP')
        self.d.cache.aufnehmen(OFF)
        await self.c.motor_neu()
        s=await self.c.lokal('STATUS')
        self.assertEqual(s['motor']['zustand'],'AUS_BEREIT')
        self.assertIs(s['stop_bestaetigt'],True)

    async def test_running_response_after_stop_is_not_confirmed(self):
        await self.c.lokal('STOP')
        self.d.cache.aufnehmen(ON)
        await self.c.motor_neu()
        s=await self.c.lokal('STATUS')
        self.assertIs(s['stop_bestaetigt'],False)
        self.assertEqual(s['zustand'],'LAEUFT_NACH_STOP')
        self.assertIn('weiterhin Drehzahl',s['fehler'])

    async def test_no_reply_is_diagnostic_not_rejected_stop(self):
        await self.c.lokal('STOP')
        s=await self.c.lokal('STATUS')
        self.assertEqual(s['motor']['zustand'],'NACH_STOP_UNBESTAETIGT')
        await asyncio.sleep(.45)
        s=await self.c.lokal('STATUS')
        self.assertIn('Keine neue MO_W',s['fehler'])
        self.assertEqual(self.d.writer.sent,[b'MO_AUS\n', b'MO_W\n'])
        self.assertIsNone(s['stop_bestaetigt'])

if __name__=='__main__': unittest.main()
