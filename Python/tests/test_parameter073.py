import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from parameter import Parameter, pruefen, lade_parameter, speichere_parameter
from dienst import Dienst

class ParameterTest(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            pfad = Path(folder) / 'regelparameter.json'
            p = replace(Parameter(), basislast_kw=0.4, plan_gesamtreserve_kwh=6.0)
            speichere_parameter(pfad, p)
            neu = lade_parameter(pfad)
            self.assertEqual(neu.basislast_kw, 0.4)
            self.assertEqual(neu.plan_gesamtreserve_kwh, 6.0)

    def test_reject_bad(self):
        with self.assertRaises(ValueError):
            pruefen(replace(Parameter(), plan_gesamtreserve_kwh=-2))
        with self.assertRaises(ValueError):
            pruefen(replace(Parameter(), solltemperatur_c=24))
        with self.assertRaises(ValueError):
            pruefen(replace(Parameter(), basislast_kw=float('nan')))

    def test_service_commands_and_restart(self):
        async def check(folder):
            d = Dienst(datenordner=folder, status_port=0)
            server = await asyncio.start_server(d.status_client, '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            async def ask(cmd):
                r, w = await asyncio.open_connection('127.0.0.1', port)
                w.write((cmd+'\n').encode()); await w.drain()
                response = json.loads(await r.readline())
                w.close(); await w.wait_closed()
                return response
            try:
                a = await ask('RE_SET basislast_kw 0.4')
                self.assertEqual(a['ergebnis'], 'PARAMETER GESETZT')
                b = await ask('RE_P')
                self.assertEqual(b['parameter']['basislast_kw'], 0.4)
                self.assertEqual(b['parameter']['plan_inselbatterie_min_kwh'], 1.5)
                bad = await ask('RE_SET plan_gesamtreserve_kwh -1')
                self.assertIn('fehler', bad)
                self.assertEqual(d.parameter.plan_gesamtreserve_kwh, 5.4)
                self.assertEqual(Dienst(datenordner=folder, status_port=0).parameter.basislast_kw, 0.4)
            finally:
                server.close(); await server.wait_closed()
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(check(folder))

if __name__ == '__main__': unittest.main()
