import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from pv_cache import PVCache

TZ = ZoneInfo('Europe/Berlin')

class PVStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_ohne_daten_und_neustart(self):
        zeit = datetime(2026, 11, 17, 0, 5, tzinfo=TZ)
        abrufe = []
        def api(kwp, neigung):
            abrufe.append((kwp, neigung))
            return {'tage': {'2026-11-17': 5000, '2026-11-18': 4000},
                    'kumuliert_wh': {'2026-11-17 12:00:00': 1234}}
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp)/'pv_cache.json'
            c = PVCache(pfad, abruf=api, jetzt=lambda: zeit)
            self.assertTrue(await c.beim_start())
            self.assertEqual(len(abrufe), 3)
            self.assertEqual(c.status()['anfragen_heute'], 3)
            log = (Path(tmp)/'pv_prognosen.jsonl').read_text().splitlines()
            self.assertEqual(len(log), 3)
            self.assertEqual(json.loads(log[0])['anlass'], 'dienststart')
            self.assertFalse(await c.beim_start())
            c2 = PVCache(pfad, abruf=api, jetzt=lambda: zeit)
            self.assertFalse(await c2.beim_start())
            self.assertEqual(len(abrufe), 3)
            zeit = datetime(2026, 11, 17, 0, 20, tzinfo=TZ)
            self.assertFalse(await c2.faellig())
            zeit = datetime(2026, 11, 17, 12, 15, tzinfo=TZ)
            self.assertTrue(await c2.faellig())
            self.assertEqual(len(abrufe), 6)
            self.assertEqual(len((Path(tmp)/'pv_prognosen.jsonl').read_text().splitlines()), 6)

    async def test_nur_fehlendes_feld(self):
        zeit = datetime(2026, 11, 17, 17, tzinfo=TZ)
        calls = []
        def api(kwp, neigung):
            calls.append(kwp)
            return {'2026-11-17': 1, '2026-11-18': 2}
        with tempfile.TemporaryDirectory() as tmp:
            pfad = Path(tmp)/'pv_cache.json'
            state = {'budget_tag': '2026-11-17', 'verbrauch': 7, 'fenster': [],
                     'prognosen_kwh': {
                         'pv1_4kwp_38grad': {'2026-11-17': 2, '2026-11-18': 2},
                         'pv2_2_5kwp_20grad': {'2026-11-17': 3, '2026-11-18': 3},
                         'fenster_sued_2_2kwp_90grad': {'2026-11-17': 1}}}
            pfad.write_text(json.dumps(state))
            c = PVCache(pfad, abruf=api, jetzt=lambda: zeit)
            self.assertTrue(await c.beim_start())
            self.assertEqual(calls, [2.2])
            self.assertEqual(c.status()['anfragen_heute'], 8)

    async def test_budget_maximal(self):
        zeit = datetime(2026, 11, 17, 20, tzinfo=TZ)
        calls=[]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'pv_cache.json'
            path.write_text(json.dumps({'budget_tag':'2026-11-17','verbrauch':11,'fenster':[]}))
            c=PVCache(path,abruf=lambda kwp, tilt: calls.append(kwp) or {'2026-11-17':1,'2026-11-18':2},jetzt=lambda:zeit)
            self.assertTrue(await c.beim_start())
            self.assertEqual(len(calls),1)
            self.assertEqual(c.status()['anfragen_heute'],12)
            self.assertFalse(await c.faellig())
