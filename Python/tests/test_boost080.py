import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from energie_boost import EnergieBoost, boost_kwh_im_intervall
from status import format_plan

class BoostTests(unittest.TestCase):
    def test_boost_timing_persist_restart_and_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'boost.json'
            now = datetime(2026, 11, 18, 19, tzinfo=timezone.utc)
            b = EnergieBoost(path)
            b.setzen(5, now)
            self.assertAlmostEqual(b.aktiv(now)['rest_kwh'], 5.0)
            self.assertAlmostEqual(b.aktiv(now + timedelta(hours=12))['rest_kwh'], 2.5)
            # Wiederholtes Senden desselben Befehls darf keinen neuen 24-h-Zeitraum ausloesen.
            b.setzen(5, now + timedelta(hours=12))
            self.assertEqual(b.aktiv(now + timedelta(hours=12))['start_utc'], now.isoformat())
            self.assertEqual(b.aktiv(now + timedelta(hours=12))['ende_utc'], (now + timedelta(hours=24)).isoformat())
            self.assertAlmostEqual(b.aktiv(now + timedelta(hours=12))['rest_kwh'], 2.5)
            # Auch nach Neustart muss der identische Befehl wirkungslos bleiben.
            b2 = EnergieBoost(path)
            b2.setzen(5, now + timedelta(hours=13))
            self.assertEqual(b2.aktiv(now + timedelta(hours=13))['ende_utc'], (now + timedelta(hours=24)).isoformat())

            self.assertAlmostEqual(boost_kwh_im_intervall(b.aktiv(now), now, now+timedelta(hours=24)), 5)
            self.assertAlmostEqual(boost_kwh_im_intervall(b.aktiv(now + timedelta(hours=12)), now+timedelta(hours=12), now+timedelta(hours=36)), 2.5)
            self.assertIsNone(b.aktiv(now + timedelta(hours=24)))
            b.setzen(7, now)
            self.assertEqual(EnergieBoost(path).aktiv(now)['energie_kwh'], 7)
            b.setzen(0, now)
            self.assertIsNone(EnergieBoost(path).aktiv(now))

    def test_input_validation(self):
        with tempfile.TemporaryDirectory() as d:
            b = EnergieBoost(Path(d) / 'boost.json')
            for x in [-1, 28, float('nan'), float('inf')]:
                with self.assertRaises(ValueError):
                    b.setzen(x)

    def test_status_without_shortage(self):
        d={'zeitpunkt':'2026-11-18T19:00:00+01:00', 'status':'SIMULIERTER PLAN GEFUNDEN',
           'auswahl':{'anzahl':2,'startzeiten_im_horizont':[],'soc_ende_prozent':45,
             'minimum_soc_prozent':20,'temperatur_min_c':21,'temperatur_max_c':23,
             'elektrischer_fehlbetrag_kwh':0.5},
           'vergleich':[{'anzahl':2,'zulaessig':False,'soc_ende_prozent':45,
              'minimum_soc_prozent':20,'temperatur_min_c':21,'temperatur_max_c':23,
              'elektrischer_fehlbetrag_kwh':0.5}]}
        out=format_plan(d)
        self.assertIn('SOC Ende',out)
        self.assertIn('SOC min',out)
        self.assertNotIn('Fehlbetrag',out)
        self.assertNotIn('Stromfehlbetrag',out)

if __name__ == '__main__':
    unittest.main()
