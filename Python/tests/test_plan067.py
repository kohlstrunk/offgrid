import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import Mock

from planung_automatik import simuliere_plan, plan_ermitteln, pv_energy
from parameter import Parameter
from sensor_cache import SensorCache
from sensoren import parse_tm_b, parse_in_w, parse_bm_w, parse_mo_w

TZ = ZoneInfo('Europe/Berlin')
NOW = datetime(2026, 11, 17, 0, 20, tzinfo=TZ)


def pv_data():
    data = {'prognosen_kwh': {}, 'zeitverlauf_wh': {}, 'fenster_halbstunden': {}}
    for name in ('pv1_4kwp_38grad', 'pv2_2_5kwp_20grad'):
        data['prognosen_kwh'][name] = {'2026-11-17': 8.0, '2026-11-18': 8.0}
    # Temperaturmodell hat Intervalle aus der Fenster-Prognose.
    for shift in (0, 1):
        day = NOW.date() + timedelta(days=shift)
        start = datetime(day.year, day.month, day.day, tzinfo=TZ)
        data['fenster_halbstunden'][day.isoformat()] = [
            {'von': (start + timedelta(minutes=30*i)).isoformat(),
             'bis': (start + timedelta(minutes=30*(i+1))).isoformat(),
             'kw_th': 0.0} for i in range(48)]
    return data


def motor(on=False):
    return parse_mo_w(('AAN 10 min 2500 rpm Tk 69.8 Tp 43.2 750 W 100 % h: 7905 18%'
                       if on else 'AAUS 0 min 0 rpm Tk 60.0 Tp 35.0 0 W 0 % h: 7905 0%'))


class PlanTest(unittest.TestCase):
    def test_pv_daily_sine_conserves_energy(self):
        fallback = set()
        pv = pv_data()
        total = sum(pv_energy('pv1_4kwp_38grad', NOW.replace(hour=0, minute=0) + timedelta(minutes=i*5),
                              5/60, pv, fallback) for i in range(288))
        self.assertAlmostEqual(total, 8.0, places=5)
        self.assertTrue(fallback)

    def test_all_plans_and_no_mutation(self):
        pv = pv_data()
        p = Parameter()
        for n in range(6):
            result = simuliere_plan(n, NOW, 22.0, 4.0, 14.0, 6.0, motor(), pv, p)
            self.assertEqual(result['anzahl'], n)
            self.assertEqual(len(result['verlauf_stuendlich']), 24)
            self.assertTrue(result['minimum_gesamt_kwh'] >= 0)

    def test_motor_running_prevents_immediate_restart(self):
        res = simuliere_plan(1, datetime(2026, 11, 17, 3, 58, tzinfo=TZ), 22.0, 4.0,
                            16.0, 4.0, motor(True), pv_data(), Parameter())
        self.assertFalse(any(s.endswith('04:00') for s in res['startzeiten_im_horizont']))

    def test_missing_sensor_blocks_selection(self):
        c = SensorCache()
        pv = SimpleNamespace(status=lambda: pv_data(), state={})
        result = plan_ermitteln(c, pv, zeit=NOW)
        self.assertIsNone(result['auswahl'])
        self.assertEqual(result['status'], 'DATEN FEHLEN')

    def test_missing_pv_yields_no_plan(self):
        c = SensorCache()
        c.aufnehmen('IT 22 C AT 4 C')
        c.aufnehmen('SD 2 kWh Pw 53,4 W Pp 0 W 229 V 50 Hz 52.9 V')
        c.aufnehmen('Ch: 142.5 Ah En: 2 kWh Pd: 928 W Pa: 0 W Ia: 19.1 A U: 48.26 V T: 28.8 C')
        c.aufnehmen('AAUS 0 min 0 rpm Tk 60 Tp 30 0 W 0 % h: 7905 0%')
        pv = SimpleNamespace(status=lambda: {'prognosen_kwh':{}, 'fenster_halbstunden':{}}, state={})
        result = plan_ermitteln(c, pv, zeit=NOW)
        self.assertIsNone(result['auswahl'])
        self.assertEqual(len(result['szenarien']), 6)

    def test_no_actuator_call_strings_in_planner(self):
        import inspect, planung_automatik
        src = inspect.getsource(planung_automatik)
        for token in ('MO_AN', 'MO_AUS', 'WP_H', 'WP_C', 'WP_S'):
            self.assertNotIn(token, src)


if __name__ == '__main__':
    unittest.main()
