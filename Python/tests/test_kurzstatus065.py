import unittest
from dienst import Dienst
from status import format_kurz

class KurzStatusTest(unittest.TestCase):
    def test_ohne_sensoren(self):
        d = Dienst(datenordner='/tmp/offgrid-status-test-065')
        result = d.bericht_kurz()
        self.assertFalse(result['sensoren']['IN_W']['gueltig'])
        self.assertFalse(result['batterien_gesamt']['gueltig'])
        self.assertIn('ALT/FEHLT', format_kurz(result))
        self.assertLessEqual(len(format_kurz(result).splitlines()), 13)

    def test_mit_sensoren(self):
        d = Dienst(datenordner='/tmp/offgrid-status-test-065')
        d.cache.aufnehmen('IT 23.2 C  AT 6.8 C')
        d.cache.aufnehmen('SD 8.203 kWh Pw 53,4 W Pp 0.0 W 229.0 V 50.0 Hz 52.9 V')
        d.cache.aufnehmen('Ch: 142.5 Ah En: 6.83 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C')
        result = d.bericht_kurz()
        self.assertTrue(result['batterien_gesamt']['gueltig'])
        self.assertAlmostEqual(result['batterien_gesamt']['soc_prozent'], 44.32, places=2)
        output = format_kurz(result)
        self.assertIn('53 W', output)
        self.assertIn('44.3%', output)
        self.assertNotIn('fenster_halbstunden', str(result))

if __name__ == '__main__':
    unittest.main()
