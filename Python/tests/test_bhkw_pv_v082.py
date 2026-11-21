import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from parameter import Parameter
from energie_elektrisch import Speicher, schritt_mit_bhkw_abschaltung
from planung_automatik import simuliere_plan
from sensoren import parse_mo_w

TZ = ZoneInfo('Europe/Berlin')
NOW = datetime(2026, 11, 18, 23, 0, tzinfo=TZ)

def pv_zero():
    data = {'prognosen_kwh':{}, 'zeitverlauf_wh':{}, 'fenster_halbstunden':{}}
    for field in ('pv1_4kwp_38grad', 'pv2_2_5kwp_20grad', 'fenster_sued_2_2kwp_90grad'):
        data['prognosen_kwh'][field] = {
            (NOW.date() + timedelta(days=i)).isoformat(): 0 for i in range(3)}
    return data

def motor():
    return parse_mo_w('AAUS 0 min 0 rpm Tk 60 Tp 30 0 W 0 % h: 7905 0%')

class TestBHKWv082(unittest.TestCase):
    def test_pv2_is_curtailed_without_bhkw_shutdown(self):
        s = Speicher(10., 7.)
        shortage, curtailed, transfer, stop, fraction = schritt_mit_bhkw_abschaltung(
            s, 0., 2., 0., 0.25, 1., Parameter())
        self.assertAlmostEqual(curtailed, 1.75)
        self.assertFalse(stop)
        self.assertAlmostEqual(s.zwei_kwh, 7.)
        self.assertAlmostEqual(transfer, 0.25)

    def test_bhkw_stops_when_small_storage_cannot_accept(self):
        s = Speicher(10., 7.)
        shortage, curtailed, transfer, stop, fraction = schritt_mit_bhkw_abschaltung(
            s, 0., 0., 0.75, 0.25, 1./12, Parameter())
        self.assertTrue(stop)
        self.assertAlmostEqual(fraction, 1./3.)
        self.assertAlmostEqual(s.zwei_kwh, 7.)
        self.assertAlmostEqual(curtailed, 0.)

    def test_no_cooling_when_bhkw_start_in_plan(self):
        p = Parameter()
        r = simuliere_plan(1, NOW, 24., 22., 16., 4., motor(), pv_zero(), p)
        self.assertEqual(r['wp_strom_kwh'], 0.)
        self.assertTrue(all(x['wp'] != 'KUEHLEN' for x in r['verlauf_stuendlich']))
        self.assertFalse(r['kuehlung_im_plan'])

    def test_automatic_bhkw_shutdown_reported(self):
        r = simuliere_plan(1, NOW.replace(hour=3, minute=58), 22., 4., 10., 7., motor(), pv_zero(), Parameter())
        self.assertTrue(r['bhkw_abschaltung_geschaetzt'])

if __name__ == '__main__':
    unittest.main()
