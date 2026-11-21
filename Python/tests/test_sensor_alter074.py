import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dienst import Dienst
from sensoren import parse_tm_b

class SensorAlterTest(unittest.TestCase):
    def test_1228_sekunden_bei_1800_gueltig(self):
        with tempfile.TemporaryDirectory() as folder:
            dienst = Dienst(datenordner=folder)
            clock = [2000.0]
            dienst.cache.clock = lambda: clock[0]
            dienst.cache._werte['TM_B'] = (parse_tm_b('IT 23.2 C AT 6.8 C'), 772.0, '2026-11-18T12:00:00+00:00')
            self.assertFalse(dienst.cache.bericht(dienst.intervall)['TM_B']['gueltig'])
            from dataclasses import replace
            dienst.parameter = replace(dienst.parameter, sensor_max_alter_s=1800)
            self.assertTrue(dienst.cache.bericht(dienst.intervall)['TM_B']['gueltig'])
            self.assertTrue(dienst.bericht_kurz()['sensoren']['TM_B']['gueltig'])
            clock[0] = 2600.0
            self.assertFalse(dienst.bericht_kurz()['sensoren']['TM_B']['gueltig'])

    def test_plan_nutzt_konfiguriertes_alter(self):
        # PV wird nur gebraucht, wenn alle Sensorwerte vorliegen;
        # deshalb hier fruehen Datencheck nachstellen.
        from planung_automatik import plan_ermitteln
        with tempfile.TemporaryDirectory() as folder:
            d = Dienst(datenordner=folder)
            clock = [2000.0]
            d.cache.clock = lambda: clock[0]
            d.cache._werte['TM_B'] = (parse_tm_b('IT 23.2 C AT 6.8 C'), 772.0, '2026-11-18T12:00:00+00:00')
            # Bei Default 600s ist TM_B unter den fehlenden Sensoren.
            p = plan_ermitteln(d.cache, d.pv, d.parameter)
            self.assertIn('TM_B', str(p.get('gruende')))
            from dataclasses import replace
            p = plan_ermitteln(d.cache, d.pv, replace(d.parameter, sensor_max_alter_s=1800))
            self.assertNotIn('TM_B', str(p.get('gruende')))

class ParameterLiveTest(unittest.IsolatedAsyncioTestCase):
    async def test_re_set_dauerhaft_und_validiert(self):
        with tempfile.TemporaryDirectory() as folder:
            d = Dienst(datenordner=folder, status_port=0)
            server = await asyncio.start_server(d.status_client, '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            async def ask(cmd):
                r,w = await asyncio.open_connection('127.0.0.1', port)
                w.write((cmd+'\n').encode()); await w.drain()
                data = json.loads(await r.readline()); w.close(); await w.wait_closed()
                return data
            try:
                x = await ask('RE_SET sensor_max_alter_s 1800')
                self.assertEqual(x['ergebnis'], 'PARAMETER GESETZT')
                self.assertEqual(d.intervall, 1800)
                self.assertEqual((await ask('RE_P'))['parameter']['sensor_max_alter_s'], 1800)
                self.assertEqual(Dienst(datenordner=folder).intervall, 1800)
                x = await ask('RE_SET min_funkpause_s 10')
                self.assertEqual(x['ergebnis'], 'PARAMETER GESETZT')
                self.assertEqual(d.funkpause, 10)
                self.assertEqual(Dienst(datenordner=folder).funkpause, 10)
                for cmd in ('RE_SET sensor_max_alter_s 1800.5', 'RE_SET sensor_max_alter_s 0', 'RE_SET min_funkpause_s -3'):
                    self.assertIn('fehler', await ask(cmd))
                self.assertEqual(d.intervall, 1800)
            finally:
                server.close(); await server.wait_closed()
