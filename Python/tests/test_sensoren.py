import unittest
from sensoren import parse_tm_b, parse_in_w, parse_bm_w
from parameter import Parameter
from bhkw_planung import pruefe_startplan

class ParserTests(unittest.TestCase):
    def test_temperaturen(self):
        x = parse_tm_b('IT 23.2 C  AT 6.8 C')
        self.assertEqual((x.innen_c, x.aussen_c), (23.2, 6.8))
    def test_in(self):
        x = parse_in_w('SD 8.203 kWh Pw 53,4 W Pp 0.0 W 229.0 V 50.0 Hz 52.9 V')
        self.assertAlmostEqual(x.entnommen_kwh, 8.203)
        self.assertAlmostEqual(x.abgabe_ac_w, 53.4)
        self.assertAlmostEqual(x.batteriespannung_v, 52.9)
    def test_bm(self):
        x = parse_bm_w('Ch: 142.5 Ah En: 6.83 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C')
        self.assertAlmostEqual(x.entnommen_kwh, 6.83)
        self.assertAlmostEqual(x.abgabe_dc_w, 928)
    def test_unvollstaendig(self):
        with self.assertRaises(ValueError): parse_in_w('SD 2 kWh')
    def test_fuenf_starts_20min_pause(self):
        self.assertTrue(pruefe_startplan((4.,9.,14.,19.,23+20/60), Parameter()))

if __name__ == '__main__': unittest.main()
