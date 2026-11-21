"""Regressionspruefung: Gesamtreserve ist Zielwert am Planende."""
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from parameter import Parameter
from planung_automatik import simuliere_plan
from sensoren import parse_mo_w

TZ = ZoneInfo('Europe/Berlin')
NOW = datetime(2026, 11, 17, 0, 20, tzinfo=TZ)
FIELDS = ('pv1_4kwp_38grad', 'pv2_2_5kwp_20grad', 'fenster_sued_2_2kwp_90grad')

def pv(pv_kwh):
    return {'prognosen_kwh': {k: {'2026-11-17': pv_kwh if k != FIELDS[2] else 0.0,
                                   '2026-11-18': pv_kwh if k != FIELDS[2] else 0.0}
                              for k in FIELDS}, 'zeitverlauf_wh': {}, 'fenster_halbstunden': {}}

def motor():
    return parse_mo_w('AAUS 0 min 0 rpm Tk 60 Tp 35 0 W 0 % h: 7905 0%')

class GesamtreserveTest(unittest.TestCase):
    def test_start_unter_reserve_aber_ende_darueber(self):
        result = simuliere_plan(0, NOW, 22, 22, 2, 2, motor(), pv(8), Parameter())
        self.assertLess(result['minimum_gesamt_kwh'], 5.4)
        self.assertGreaterEqual(result['energie_ende_kwh'], 5.4)
        self.assertTrue(result['zulaessig'], result['gruende'])
        self.assertAlmostEqual(result['soc_ende_prozent'], 100 * result['energie_ende_kwh']/27, delta=0.1)

    def test_ende_unter_reserve_bleibt_unzulaessig(self):
        result = simuliere_plan(0, NOW, 22, 22, 2, 2, motor(), pv(0), Parameter())
        self.assertLess(result['energie_ende_kwh'], 5.4)
        self.assertFalse(result['zulaessig'])
        self.assertIn('Gesamtreserve am Planende unterschritten', result['gruende'])

    def test_inselreserve_bleibt_separat(self):
        result = simuliere_plan(0, NOW, 22, 22, 1, 3, motor(), pv(8), Parameter())
        self.assertFalse(result['zulaessig'])
        self.assertTrue(any('Inselbatterie' in reason for reason in result['gruende']))

if __name__ == '__main__':
    unittest.main()
