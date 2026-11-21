import unittest
from datetime import datetime
from dataclasses import replace
from zoneinfo import ZoneInfo

from parameter import Parameter
from wp_vorausschau import wp_start_prognose

TZ = ZoneInfo('Europe/Berlin')
TAG = '2026-06-20'


def pv(p1=12, p2=7, fenster=2):
    return {'prognosen_kwh': {'pv1_4kwp_38grad': {TAG:p1},
                             'pv2_2_5kwp_20grad':{TAG:p2},
                             'fenster_sued_2_2kwp_90grad':{TAG:fenster}},
            'zeitverlauf_wh':{}}


class PrognoseTests(unittest.TestCase):
    def setUp(self):
        self.p = replace(Parameter(), maximum_c=25)
        self.zeit = datetime(2026,6,20,8,0,tzinfo=TZ)

    def test_early_dynamic_soc_below_fixed_96(self):
        r=wp_start_prognose(self.zeit,pv(),self.p,22,18,18,5)
        self.assertEqual(r['grund'],'WP_START_SPÄTER')
        self.assertLess(r['einschalt_soc_berechnet_prozent'],96)
        self.assertGreater(r['pv_abregelung_ohne_wp_kwh'],r['pv_abregelung_mit_wp_kwh'])
        self.assertGreater(r['soc_ende_mit_wp_prozent'],99)
        self.assertLess(datetime.fromisoformat(r['einschaltzeit']),
                        datetime.fromisoformat(r['ausschaltzeit_prognose']))

    def test_no_pv_spill_means_no_wp(self):
        r=wp_start_prognose(self.zeit,pv(.1,.1,.1),self.p,22,18,10,3)
        self.assertFalse(r['starten'])
        self.assertEqual(r['grund'],'KEINE_DROHENDE_ABREGELUNG')

    def test_missing_forecast_never_starts(self):
        r=wp_start_prognose(self.zeit,{'prognosen_kwh':{},'zeitverlauf_wh':{}},self.p,22,18,19,5)
        self.assertFalse(r['starten'])
        self.assertEqual(r['grund'],'PV_PROGNOSE_FEHLT')

    def test_max_temperature_limits_thermal_use(self):
        r=wp_start_prognose(self.zeit,pv(),self.p,24.9,18,18,5)
        self.assertLessEqual(r.get('temp_ende_mit_wp_c',25),25)
        self.assertGreater(r.get('pv_abregelung_mit_wp_kwh',0),0)

    def test_evening_no_retroactive_start(self):
        r=wp_start_prognose(datetime(2026,6,20,19,0,tzinfo=TZ),pv(),self.p,22,18,19,6)
        self.assertFalse(r['starten'])
        self.assertIn(r['grund'], ('PV_TAG_BEENDET', 'KEIN_PV_ERTRAG_MEHR'))

if __name__=='__main__': unittest.main()
