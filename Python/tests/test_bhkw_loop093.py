"""Regressionstests v0.9.3: Motor-Events, Neustart-STOP, Antwortprotokoll."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bhkw_steuerung import BHKWSteuerung
from bhkw_lokal import senden
from dienst import Dienst
from sensor_cache import SensorCache

OFF = 'AAUS 0 min 0 rpm Tk 35.0 Tp 25.0 0 W 0 % h: 7900 0%'
ON = 'AAN 1 min 2500 rpm Tk 60.0 Tp 45.0 750 W 0 % h: 7900 18%'

class Writer:
    def __init__(self):
        self.sent=[]
    def is_closing(self): return False
    def write(self, data): self.sent.append(data)
    async def drain(self): pass

class Regression093(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.d=SimpleNamespace(datenordner=Path(self.tmp.name), cache=SensorCache(),
            connected=True, writer=Writer(), ausstehend=None,
            letztes_senden=float('-inf'), funkpause=0, timeout=1,
            ausstehend_seit=0, funk_lock=asyncio.Lock())
        self.c=BHKWSteuerung(self.d)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_motor_event_does_not_wait_for_local_lock(self):
        """Der Receiver muss trotz laufender Zustandsoperation vorankommen."""
        await self.c._lock.acquire()
        try:
            self.c.zustand='START_AUSSTEHEND'
            self.c.letzter_status_timestamp=None
            self.d.cache.aufnehmen(ON)
            await asyncio.wait_for(self.c.motor_neu(), timeout=0.2)
            self.assertEqual(self.c.zustand, 'LAEUFT')
        finally:
            self.c._lock.release()

    async def test_reject_cross_event_loop(self):
        await self.c.motor_neu()  # Owner im Hauptloop setzen
        def other_loop():
            return asyncio.run(self.c.motor_neu())
        with self.assertRaisesRegex(RuntimeError, 'falschen Event-Loop'):
            await asyncio.to_thread(other_loop)

    async def test_stale_restart_error_cleared_on_stop_sent(self):
        self.d.cache.aufnehmen(ON)
        self.c.recovery={'lauf_ungeklaert': True, 'start_utc': '2026-11-19T00:00:00+00:00'}
        self.c.zustand='SPERRE_NACH_NEUSTART'
        self.c.fehler='Vorheriger BHKW-Lauf ungeklaert; lokal pruefen und bestaetigen'
        await self.c.lokal('STOP')
        self.assertEqual(self.c.zustand, 'STOP_GESENDET')
        self.assertIsNone(self.c.fehler)
        self.assertEqual(self.d.writer.sent, [b'MO_AUS\n'])

    async def test_unix_socket_success_response_is_explicit(self):
        d=Dienst(datenordner=self.tmp.name)
        async def client(reader, writer):
            await d.lokaler_aktor_client(reader, writer)
        socket=Path(self.tmp.name)/'test.sock'
        server=await asyncio.start_unix_server(client, path=str(socket))
        try:
            await asyncio.sleep(0)
            result=await senden(socket, 'STATUS')
            self.assertIs(result['ok'], True)
            self.assertIn('bhkw_teststeuerung', result)
            result=await senden(socket, 'UNBEKANNT')
            self.assertIs(result['ok'], False)
            self.assertIn('fehler', result)
        finally:
            server.close()
            await server.wait_closed()

if __name__=='__main__': unittest.main()
