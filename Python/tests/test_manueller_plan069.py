import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from manueller_plan import ManuellerPlan
from dienst import Dienst
from status import format_plan

class ManualTests(unittest.TestCase):
    def test_set_persist_expire_cancel(self):
        with tempfile.TemporaryDirectory() as root:
            t = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
            p = ManuellerPlan(Path(root)/'manual.json')
            v = p.setzen(5, t)
            self.assertEqual(v['schema'], 5)
            new = ManuellerPlan(Path(root)/'manual.json')
            self.assertEqual(new.aktiv(t + timedelta(hours=23))['schema'], 5)
            self.assertIsNone(new.aktiv(t + timedelta(hours=24)))
            new.aufheben()
            self.assertIsNone(ManuellerPlan(Path(root)/'manual.json').aktiv())
    def test_invalid(self):
        with tempfile.TemporaryDirectory() as root:
            p=ManuellerPlan(Path(root)/'manual.json')
            for bad in (-1, 6, 1.1, '2', True):
                with self.assertRaises(ValueError):
                    p.setzen(bad)
    def test_service_commands(self):
        async def run():
            with tempfile.TemporaryDirectory() as root:
                service = Dienst(datenordner=root)
                async def request(command):
                    incoming = asyncio.StreamReader()
                    incoming.feed_data((command+'\n').encode())
                    incoming.feed_eof()
                    class Writer:
                        def __init__(self): self.out=b''
                        def write(self,b): self.out+=b
                        async def drain(self): pass
                        def close(self): pass
                        async def wait_closed(self): pass
                    w=Writer()
                    await service.status_client(incoming, w)
                    import json
                    return json.loads(w.out)
                r=await request('RE_PLAN_SET 3')
                self.assertEqual(r['manuelle_vorgabe']['schema'],3)
                out=await request('RE_PLAN')
                self.assertEqual(out['manuelle_vorgabe']['schema'],3)
                self.assertEqual(out['auswahl']['anzahl'],3)
                self.assertIn('MANUELL',format_plan(out))
                bad=await request('RE_PLAN_SET 7')
                self.assertIn('fehler',bad)
                await request('RE_PLAN_AUTO')
                self.assertIsNone((await request('RE_PLAN'))['manuelle_vorgabe'])
        asyncio.run(run())
