import unittest
from parameter import Parameter, Anfangszustand, STARTPLAENE
from bhkw_planung import Prognose, simuliere, pruefe_startplan, waehle
from energie_thermisch import temperatur_nach_schritt
from sicherheit import AktorenGesperrt, sende_aktor_befehl

class Tests(unittest.TestCase):
    def setUp(self):
        self.p = Parameter()

    def test_startplaene(self):
        for zeiten in STARTPLAENE.values():
            self.assertTrue(pruefe_startplan(zeiten, self.p))

    def test_bhkw_waermt(self):
        t = temperatur_nach_schritt(22, 4, True, 'STOP', 1, self.p)
        self.assertGreater(t, 22)

    def test_durchlaeufe(self):
        for n in STARTPLAENE:
            result = simuliere(n, Anfangszustand(), Prognose(4, 8, 5), self.p)
            self.assertEqual(result.starts, n)
            self.assertEqual(len(result.verlauf), 24)

    def test_nicht_zulaessig_ist_keine_freigabe(self):
        _, _, erlaubt = waehle(Anfangszustand(16, 0, 0), Prognose(-15, 0, 0), self.p)
        self.assertFalse(erlaubt)

    def test_keine_aktorfreigabe(self):
        with self.assertRaises(AktorenGesperrt):
            sende_aktor_befehl('MO_AN')

if __name__ == '__main__':
    unittest.main()
