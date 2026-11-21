import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from pv_cache import PVCache
from solar_waerme import fenster_waerme_halbstunden
from thermik_live import prognose_thermik

BERLIN = ZoneInfo('Europe/Berlin')


class ZeitverlaufTests(unittest.TestCase):
    def test_halbstunden_kumuliert_ergibt_tagesenergie(self):
        wh = {'2026-11-16 09:00:00': 0,
              '2026-11-16 10:00:00': 1000,
              '2026-11-16 11:00:00': 2000,
              '2026-11-16 12:00:00': 3000,
              '2026-11-16 13:00:00': 4000}
        v = fenster_waerme_halbstunden(wh, {'2026-11-16':4}, datetime(2026,11,16).date())
        heute = v['2026-11-16']
        self.assertEqual(len(heute), 48)
        self.assertAlmostEqual(sum(x['kwh_th'] for x in heute), 12.0, places=3)
        self.assertAlmostEqual(heute[20]['kw_th'], 3.0, places=3)  # 10:00-10:30
        self.assertIsNone(v['2026-11-17'])

    def test_thermik_sonne_erwaermt_haus(self):
        wh = {'2026-11-16 12:00:00': 0, '2026-11-16 13:00:00': 1000}
        v = fenster_waerme_halbstunden(wh, {'2026-11-16':1}, datetime(2026,11,16).date())
        t = datetime(2026,11,16,12,0,tzinfo=BERLIN)
        mit = prognose_thermik(22,22,zeit=t,fenster_halbstunden=v)
        ohne = prognose_thermik(22,22,zeit=t)
        self.assertGreater(mit['szenarien'][0]['innen_nach_24h_c'], ohne['szenarien'][0]['innen_nach_24h_c'])
        self.assertEqual(mit['schritt_min'], 30)
        self.assertEqual(mit['szenarien'][0]['fensterwaerme_im_horizont_kwh'], 3.0)

    def test_alter_cache_ohne_zeitverlauf(self):
        v = fenster_waerme_halbstunden({}, {'2026-11-16': 4}, datetime(2026,11,16).date())
        self.assertIsNone(v['2026-11-16'])


class PVCache064Tests(unittest.IsolatedAsyncioTestCase):
    async def test_zeitverlauf_ohne_weitere_requests(self):
        count=[]
        def abruf(kwp, neigung):
            count.append(kwp)
            return {'tage': {'2026-11-16': 1., '2026-11-17': 2.},
                    'kumuliert_wh': {'2026-11-16 12:00:00': 0, '2026-11-16 13:00:00': 1000}}
        with tempfile.TemporaryDirectory() as folder:
            datei=Path(folder)/'pv.json'
            now=datetime(2026,11,16,12,15,tzinfo=BERLIN)
            cache=PVCache(datei,abruf=abruf,jetzt=lambda:now)
            await cache.faellig()
            self.assertEqual(len(count),3)
            self.assertEqual(cache.status()['anfragen_heute'],3)
            self.assertEqual(len(cache.status()['fenster_halbstunden']['2026-11-16']),48)
            self.assertEqual(cache.status()['fenster_waerme']['heute_kwh_th'],3.0)
            cache2=PVCache(datei,abruf=abruf,jetzt=lambda:now)
            self.assertFalse(await cache2.faellig())
            self.assertEqual(len(count),3)
