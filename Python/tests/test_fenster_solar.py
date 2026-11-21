import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo
from pv_cache import PVCache
from solar_waerme import fenster_waerme_tage
from planung_live import status_plan

BERLIN = ZoneInfo('Europe/Berlin')


class FensterTests(unittest.IsolatedAsyncioTestCase):
    async def test_drei_abrufe_und_waerme_ausgabe(self):
        calls = []
        def abruf(kwp, neigung):
            calls.append((kwp, neigung))
            return {'2026-11-16': 4, '2026-11-17': 2}
        with TemporaryDirectory() as d:
            datei = Path(d) / 'pv_cache.json'
            zeit = datetime(2026, 11, 16, 12, 15, tzinfo=BERLIN)
            c = PVCache(datei, abruf=abruf, jetzt=lambda: zeit)
            self.assertTrue(await c.faellig())
            self.assertEqual(calls, [(4.0, 38), (2.5, 20), (2.2, 90)])
            self.assertEqual(c.status()['anfragen_heute'], 3)
            self.assertEqual(c.status()['fenster_waerme']['heute_kwh_th'], 12)
            self.assertEqual(c.status()['fenster_waerme']['morgen_kwh_th'], 6)
            self.assertEqual(status_plan(c.status(), True)['fenster_waerme']['morgen_kwh_th'], 6)
            c2 = PVCache(datei, abruf=abruf, jetzt=lambda: zeit)
            self.assertFalse(await c2.faellig())
            self.assertEqual(c2.status()['fenster_waerme']['heute_kwh_th'], 12)
            self.assertEqual(len(calls), 3)

    async def test_budget_durch_dritten_abruf_nicht_ueberschritten(self):
        with TemporaryDirectory() as d:
            zeit = [datetime(2026, 11, 16, 0, 15, tzinfo=BERLIN)]
            calls = []
            def abruf(kwp, neigung):
                calls.append((kwp, neigung))
                return {'2026-11-16': 1}
            c = PVCache(Path(d) / 'pv.json', limit=4, abruf=abruf, jetzt=lambda: zeit[0])
            await c.faellig()
            zeit[0] = datetime(2026, 11, 16, 12, 15, tzinfo=BERLIN)
            await c.faellig()
            self.assertEqual(len(calls), 4)
            self.assertEqual(c.status()['anfragen_heute'], 4)

    async def test_fehler_keine_falsche_nullwaerme(self):
        result = fenster_waerme_tage({}, datetime(2026,11,16).date())
        self.assertIsNone(result['heute_kwh_th'])
        self.assertIsNone(result['morgen_kwh_th'])

if __name__ == '__main__':
    unittest.main()
