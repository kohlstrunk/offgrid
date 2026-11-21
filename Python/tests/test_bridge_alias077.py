"""Port-1234 Bridge nutzt direkte RE-Befehle statt der veralteten Alias-Umsetzung."""
import unittest
import sys
from unittest.mock import MagicMock
sys.modules.setdefault('serial_asyncio', MagicMock())
import bridge_asyncio_re

class BridgeCommandsTest(unittest.TestCase):
    def test_bridge_still_exposes_re_dispatch(self):
        self.assertTrue(callable(bridge_asyncio_re.dispatch_re_command))
    def test_bridge_formatting_includes_end_soc(self):
        from bridge_asyncio_re import format_plan
        data = {'zeitpunkt':'2026-11-18T00:00:00', 'status':'SIMULATION',
                'auswahl': {'anzahl':0, 'startzeiten_im_horizont':[],
                            'soc_ende_prozent':23.0, 'minimum_soc_prozent':12.0,
                            'temperatur_min_c':21, 'temperatur_max_c':23,
                            'elektrischer_fehlbetrag_kwh':0}, 'vergleich':[]}
        text = format_plan(data)
        self.assertIn('SOC Ende 23.0%', text)
        self.assertIn('Minimum 12.0%', text)
