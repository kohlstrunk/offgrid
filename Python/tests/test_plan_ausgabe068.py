import unittest
from status import format_plan

class AusgabeTest(unittest.TestCase):
    def test_plan_kurz(self):
        d={'zeitpunkt':'2026-11-17T12:00:00+01:00','status':'SIMULIERTER PLAN GEFUNDEN',
           'auswahl':{'anzahl':2,'startzeiten_im_horizont':['2026-11-17T17:00+01:00','2026-11-18T04:00+01:00'],
                      'minimum_soc_prozent':25,'temperatur_min_c':21.3,'temperatur_max_c':22.9,'elektrischer_fehlbetrag_kwh':0},
           'vergleich':[{'anzahl':2,'zulaessig':True,'minimum_soc_prozent':25,'temperatur_min_c':21.3,
                         'temperatur_max_c':22.9,'elektrischer_fehlbetrag_kwh':0}]}
        s=format_plan(d)
        self.assertIn('Auswahl: 2 Starts/Tag',s)
        self.assertIn('17:00',s)
        self.assertIn('04:00+2026-11-18',s)
        self.assertNotIn('{',s)
    def test_missing(self):
        s=format_plan({'status':'DATEN FEHLEN','gruende':['Sensor fehlt']})
        self.assertIn('Sensor fehlt',s)
