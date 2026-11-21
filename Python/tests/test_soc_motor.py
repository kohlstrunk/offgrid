import unittest
from sensoren import parse_mo_w, soc_aus_entnahme
from sensor_cache import SensorCache

class SoCUndMotorTests(unittest.TestCase):
    def test_soc(self):
        self.assertEqual(soc_aus_entnahme(0, 20), 100.0)
        self.assertEqual(soc_aus_entnahme(8.203, 20), 58.99)
        self.assertAlmostEqual(soc_aus_entnahme(6.83, 7), 2.43)
        self.assertEqual(soc_aus_entnahme(7, 7), 0.0)
        self.assertIsNone(soc_aus_entnahme(7.1, 7))
    def test_mo_parser(self):
        m = parse_mo_w('10 min 2500 rpm Tk 69.8 Tp 43.2 750 W 100 % h: 7905 18%')
        self.assertEqual(m.minuten_seit_start, 10)
        self.assertEqual(m.drehzahl_rpm, 2500)
        self.assertEqual(m.betriebsstunden_h, 7905)
        self.assertEqual(m.wirkungsgrad_prozent, 18)
        self.assertEqual(m.zustand, 'LAEUFT')
    def test_cache(self):
        c = SensorCache()
        self.assertEqual(c.aufnehmen('10 min 2500 rpm Tk 69.8 Tp 43.2 750 W 100 % h: 7905 18%'), 'MO_W')
        self.assertEqual(c.bericht()['MO_W']['werte']['leistung_w'], 750.0)
    def test_unvollstaendig(self):
        with self.assertRaises(ValueError):
            parse_mo_w('10 min 2500 rpm')
