import unittest
from sensor_cache import SensorCache
from sensoren import parse_in_w

class GesamtsocTests(unittest.TestCase):
    def test_beide_aktuell(self):
        t = [100.0]
        c = SensorCache(clock=lambda: t[0])
        self.assertFalse(c.gesamt_soc()['gueltig'])
        self.assertEqual(c.aufnehmen('SD 8.203 kWh Pw 53,4 W Pp 0.0 W 229.0 V 50.0 Hz 52.9 V'), 'IN_W')
        self.assertEqual(c.aufnehmen('Ch: 142.5 Ah En: 6.83 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C'), 'BM_W')
        self.assertEqual(c.gesamt_soc()['restenergie_kwh'], 11.967)
        self.assertEqual(c.gesamt_soc()['soc_prozent'], 44.32)
        t[0] += 601
        self.assertFalse(c.gesamt_soc()['gueltig'])
    def test_in_w_parse_diagnose(self):
        c = SensorCache()
        self.assertIsNone(c.aufnehmen('SD 8.203 kWh Pw 53,4 W Pp 0.0 W'))
        self.assertEqual(c.diagnose['IN_W']['parserfehler'], 1)
        self.assertEqual(c.aufnehmen('SD 8,203 kWh Pw 53,4 W Pp 0,0 W 229,0 V 50,0 Hz 52,9 V'), 'IN_W')
        self.assertEqual(c.bericht()['IN_W']['diagnose']['gueltige_antworten'], 1)
    def test_no_fabricated_soc(self):
        c = SensorCache()
        c.aufnehmen('SD 8.203 kWh Pw 53,4 W Pp 0.0 W 229.0 V 50.0 Hz 52.9 V')
        self.assertFalse(c.gesamt_soc()['gueltig'])

if __name__ == '__main__': unittest.main()
