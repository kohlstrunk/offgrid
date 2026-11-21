import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from parameter import Parameter
from planung_automatik import simuliere_plan
from sensoren import parse_mo_w

TZ = ZoneInfo('Europe/Berlin')
MOTOR = parse_mo_w('AAUS 0 min 0 rpm Tk 60 Tp 30 0 W 0 % h: 7905 0%')
FELDER = ('pv1_4kwp_38grad','pv2_2_5kwp_20grad','fenster_sued_2_2kwp_90grad')
def prognose(now):
    return {'prognosen_kwh': {f:{(now.date()+timedelta(days=i)).isoformat():0 for i in range(4)} for f in FELDER}, 'zeitverlauf_wh':{}, 'fenster_halbstunden':{}}

class TestSchaltzeiten083(unittest.TestCase):
    def test_kein_bhkw_start_vor_14_uhr(self):
        now = datetime(2026,11,18,23,25,48,tzinfo=TZ)
        # Sehr voller kleiner Akku: BHKW muss beim 14:00-Lauf abschalten.
        # Auch wenn das Fuenf-Minuten-Raster 13:55:48..14:00:48 lautet,
        # darf das Modell den Lauf NICHT 13:55 beginnen lassen.
        r = simuliere_plan(4, now, 22.0, 4.0, 9.0, 6.99, MOTOR, prognose(now), Parameter())
        laeufe = [x for x in r['bhkw_laeufe'] if 'T14:00' in x['plan_start']]
        self.assertEqual(len(laeufe), 1)
        self.assertIsNotNone(laeufe[0]['ist_start'])
        self.assertEqual(laeufe[0]['ist_start'][11:16], '14:00')
        if laeufe[0]['ist_ende']:
            self.assertGreaterEqual(laeufe[0]['ist_ende'], laeufe[0]['ist_start'])

    def test_regulaeres_ende_um_13_uhr(self):
        now = datetime(2026,11,18,23,25,48,tzinfo=TZ)
        # Die kleine Batterie nicht sofort voll: regulaere Laufzeit 9..13.
        r = simuliere_plan(4, now, 22.0, 4.0, 8.0, 0.0, MOTOR, prognose(now), Parameter())
        lauf = next(x for x in r['bhkw_laeufe'] if 'T09:00' in x['plan_start'])
        self.assertEqual(lauf['plan_ende'][11:16], '13:00')
        self.assertEqual(lauf['ist_start'][11:16], '09:00')
        if lauf['grund'] == 'REGULAER_4H':
            self.assertEqual(lauf['ist_ende'][11:16], '13:00')

    def test_23_20_start_erst_ab_23_20(self):
        now = datetime(2026,11,18,23,16,40,tzinfo=TZ)
        r = simuliere_plan(5, now, 22, 4, 10, 6.99, MOTOR, prognose(now), Parameter())
        run = next(x for x in r['bhkw_laeufe'] if 'T23:20' in x['plan_start'])
        self.assertEqual(run['ist_start'][11:16], '23:20')

if __name__ == '__main__':
    unittest.main()
