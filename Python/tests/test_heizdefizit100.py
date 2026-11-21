import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from planung_automatik import plan_ermitteln
from parameter import Parameter
from bhkw_automatik import BHKWAutomatik
from status import format_plan
from unittest.mock import Mock
import tempfile
from pathlib import Path

TZ = ZoneInfo('Europe/Berlin')
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=TZ)

class Cache:
    def veraltet(self, name, sec): return False
    def gesamt_soc(self, sec): return {'gueltig': True}
    def __init__(self):
        self._werte = {
            'TM_B': (SimpleNamespace(innen_c=21.5,aussen_c=4.0),),
            'MO_W': (SimpleNamespace(drehzahl_rpm=0),),
            'IN_W': (SimpleNamespace(entnommen_kwh=7.0),),
            'BM_W': (SimpleNamespace(entnommen_kwh=3.0),),
        }

class TestHeizdefizit(unittest.TestCase):
    def select(self, scenarios):
        pv=SimpleNamespace(status=lambda: {}, state={})
        with patch('planung_automatik.simuliere_plan', side_effect=lambda n,*args,**kwargs: scenarios[n]):
            return plan_ermitteln(Cache(),pv,p=Parameter(minimum_c=22,solltemperatur_c=23,maximum_c=24),zeit=NOW)

    @staticmethod
    def case(n, reasons):
        return {'anzahl':n,'zulaessig':not reasons,'gruende':reasons,
                'startzeiten_im_horizont':['2026-11-19T20:00+01:00'],
                'temperatur_min_c':21.5,'temperatur_max_c':23.5,
                'soc_ende_prozent':30,'minimum_soc_prozent':25,
                'pv_ueberschuss_kwh':0}

    def test_thermal_only_falls_back_to_five(self):
        x=[self.case(n,['Raumtemperatur unter Komfortgrenze']) for n in range(6)]
        out=self.select(x)
        self.assertEqual(out['auswahl']['anzahl'],5)
        self.assertFalse(out['auswahl']['zulaessig'])
        self.assertTrue(out['auswahl']['automatisch_startbar'])
        self.assertIn('HEIZDEFIZIT',out['status'])
        self.assertIn('HEIZDEFIZIT',format_plan(out))

    def test_other_violations_remain_blocking(self):
        x=[self.case(n,['Raumtemperatur unter Komfortgrenze', 'Gesamtreserve am Planende unterschritten']) for n in range(6)]
        out=self.select(x)
        self.assertIsNone(out['auswahl'])

    def test_fallback_avoids_overheating_schema(self):
        x=[self.case(n,['Raumtemperatur unter Komfortgrenze']) for n in range(6)]
        x[5]['gruende'].append('Raumtemperatur ueber Komfortgrenze')
        out=self.select(x)
        self.assertEqual(out['auswahl']['anzahl'],4)

    def test_fully_valid_scenario_has_priority(self):
        x=[self.case(n,['Raumtemperatur unter Komfortgrenze']) for n in range(6)]
        x[2]=self.case(2,[])
        out=self.select(x)
        self.assertEqual(out['auswahl']['anzahl'],2)
        self.assertNotIn('automatisch_startbar',out['auswahl'])

if __name__ == '__main__':
    unittest.main()
