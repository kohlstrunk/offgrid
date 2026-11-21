import unittest
from datetime import datetime, timedelta, timezone
from energiebilanz import Energiebilanz
from sensoren import parse_in_w, parse_bm_w
from thermik_live import prognose_thermik
from planung_live import status_plan


class LiveTests(unittest.TestCase):
    def test_zaehlerdifferenz_und_integral(self):
        b = Energiebilanz()
        t = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        a = parse_in_w('SD 8.203 kWh Pw 100 W Pp 0 W 229 V 50 Hz 52.9 V')
        c = parse_in_w('SD 8.253 kWh Pw 200 W Pp 50 W 229 V 50 Hz 52.9 V')
        b.aufnehmen('IN_W', a, t)
        b.aufnehmen('IN_W', c, t + timedelta(minutes=10))
        d = b.bericht()['IN_W']
        self.assertAlmostEqual(d['zaehlerzuwachs_seit_start_kwh'], .05)
        self.assertAlmostEqual(d['abgabe_aus_leistungsintegral_kwh'], .025)

    def test_zaehler_reset_und_luecke(self):
        b = Energiebilanz()
        t = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        a = parse_bm_w('Ch: 142.5 Ah En: 6.83 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C')
        c = parse_bm_w('Ch: 142.5 Ah En: 0.01 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C')
        b.aufnehmen('BM_W', a, t)
        b.aufnehmen('BM_W', c, t + timedelta(hours=2))
        d = b.bericht()['BM_W']
        self.assertEqual(d['zaehler_resets'], 1)
        self.assertEqual(d['integrierte_intervalle'], 0)

    def test_thermik_und_status_sind_keine_freigabe(self):
        t = datetime(2026, 11, 15, 23, 10, tzinfo=timezone.utc)
        res = prognose_thermik(22, 4, zeit=t)
        self.assertEqual(len(res['szenarien']), 6)
        self.assertTrue(res['nur_diagnose'])
        self.assertEqual(res['szenarien'][5]['startzeiten'][-1], '23:20')
        plan = status_plan({'prognosen_kwh': {}}, True)
        self.assertFalse(plan['automatische_aktorbefehle'])
        self.assertIn('verfuegbare_startschemata', plan)

if __name__ == '__main__':
    unittest.main()
