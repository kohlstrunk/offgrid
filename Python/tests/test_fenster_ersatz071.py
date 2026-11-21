import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from planung_automatik import fenster_energy, simuliere_plan
from solar_waerme import FENSTER_FELD
from tests.test_plan067 import pv_data, motor
from parameter import Parameter
from status import format_plan

TZ = ZoneInfo('Europe/Berlin')
START = datetime(2026, 11, 17, 0, 0, tzinfo=TZ)

class FensterErsatzTest(unittest.TestCase):
    def daten(self, with_daily=True):
        d = pv_data()
        d['fenster_halbstunden'] = {}
        if with_daily:
            d['prognosen_kwh'][FENSTER_FELD] = {'2026-11-17': 4., '2026-11-18': 5.}
        return d

    def test_tagesenergie_bleibt_erhalten(self):
        fallback = set()
        d = self.daten()
        total = sum(fenster_energy(START + timedelta(minutes=5*i),
                     START + timedelta(minutes=5*(i+1)), d, fallback) for i in range(288))
        self.assertAlmostEqual(total, 12., places=5)
        self.assertIn(FENSTER_FELD + '/2026-11-17', fallback)

    def test_echte_halbstunden_werden_bevorzugt(self):
        d = self.daten()
        d['fenster_halbstunden']['2026-11-17'] = [
            {'von': (START+timedelta(minutes=30*i)).isoformat(),
             'bis': (START+timedelta(minutes=30*(i+1))).isoformat(),
             'kw_th': 1.0} for i in range(48)]
        used = set()
        amount = fenster_energy(START + timedelta(hours=12),
                                START + timedelta(hours=12,minutes=5), d, used)
        self.assertAlmostEqual(amount, 1/12)
        self.assertFalse(used)

    def test_fehlende_tagesprognose_bleibt_fehler(self):
        self.assertIsNone(fenster_energy(START, START+timedelta(minutes=5),
                                         self.daten(False), set()))

    def test_simulation_kein_fenster_fehlalarm(self):
        result = simuliere_plan(0, START+timedelta(minutes=20), 22., 4.,
                               15., 6., motor(), self.daten(), Parameter())
        self.assertFalse(any('Prognose fehlt: Fenster/' in r for r in result['gruende']))
        self.assertIn(FENSTER_FELD + '/2026-11-17', result['ersatzprofile'])

    def test_kompakter_status_kennzeichnet_ersatzprofil(self):
        result = format_plan({'status': 'MANUELLER PLAN (SIMULATION)',
            'auswahl': {'anzahl': 2, 'startzeiten_im_horizont': [],
                        'ersatzprofile': [FENSTER_FELD + '/2026-11-17']}})
        self.assertIn('Fensterwaerme zeitlich geschaetzt', result)

    def test_nachtgrenze_nutzt_beide_tage(self):
        d = self.daten()
        self.assertAlmostEqual(fenster_energy(START + timedelta(hours=23,minutes=55),
                           START + timedelta(days=1,minutes=5),d,set()),0.)

if __name__ == '__main__':
    unittest.main()
