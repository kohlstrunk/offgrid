import asyncio
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo
from pv_cache import PVCache
from sensor_cache import SensorCache


class SensorCacheTests(unittest.TestCase):
    def test_mithoeren_und_alter(self):
        clock = [1000.]
        cache = SensorCache(clock=lambda: clock[0])
        self.assertTrue(cache.veraltet('TM_B'))
        self.assertEqual(cache.aufnehmen('IT 23.2 C  AT 6.8 C'), 'TM_B')
        self.assertFalse(cache.veraltet('TM_B'))
        clock[0] += 599
        self.assertFalse(cache.veraltet('TM_B'))
        clock[0] += 1
        self.assertTrue(cache.veraltet('TM_B'))
        self.assertIsNone(cache.aufnehmen('IT ungültig'))
        self.assertTrue(cache.veraltet('TM_B'))
        self.assertEqual(cache.aufnehmen('SD 8.203 kWh Pw 53,4 W Pp 0.0 W 229.0 V 50.0 Hz 52.9 V'), 'IN_W')
        self.assertEqual(cache.bericht()['IN_W']['werte']['notstrom_dc_w'], 0.0)


class PVCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_drei_anfragen_pro_fenster_und_persistenz(self):
        berlin = ZoneInfo('Europe/Berlin')
        zeit = [datetime(2026, 11, 14, 0, 10, tzinfo=berlin)]
        calls = []
        def abruf(kwp, neigung):
            calls.append((kwp, neigung))
            return {'2026-11-14': 2200, '2026-11-15': 1000}
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'pv.json'
            cache = PVCache(file, abruf=abruf, jetzt=lambda: zeit[0])
            self.assertFalse(await cache.faellig())
            zeit[0] = datetime(2026, 11, 14, 0, 15, tzinfo=berlin)
            self.assertTrue(await cache.faellig())
            self.assertFalse(await cache.faellig())
            self.assertEqual(len(calls), 3)
            cache = PVCache(file, abruf=abruf, jetzt=lambda: zeit[0])
            self.assertFalse(await cache.faellig())
            zeit[0] = datetime(2026, 11, 14, 12, 15, tzinfo=berlin)
            self.assertTrue(await cache.faellig())
            self.assertEqual(len(calls), 6)
            self.assertEqual(cache.status()['anfragen_heute'], 6)
            self.assertEqual(cache.status()['prognosen_kwh']['pv1_4kwp_38grad']['2026-11-14'], 2200)

    async def test_fehler_verbraucht_budget_und_keine_wiederholung(self):
        berlin = ZoneInfo('Europe/Berlin')
        def kaputt(*args):
            raise RuntimeError('offline')
        with tempfile.TemporaryDirectory() as tmp:
            cache = PVCache(Path(tmp)/'pv.json', abruf=kaputt,
                            jetzt=lambda: datetime(2026, 11, 14, 13, tzinfo=berlin))
            await cache.faellig()
            self.assertEqual(cache.status()['anfragen_heute'], 3)
            self.assertFalse(await cache.faellig())
