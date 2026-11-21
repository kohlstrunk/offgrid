import asyncio
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock
from parameter import Parameter, pruefen
from bhkw_automatik import BHKWAutomatik
from wp_automatik import WPAutomatik

class WpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.d=Mock()
        self.d.datenordner=Path(self.temp.name)
        self.d.parameter=Parameter()
        self.d.intervall=600
        self.d.cache.motorstatus.return_value={'zustand':'AUS_BEREIT'}
        self.d.bhkw._bridge_bereit.return_value=True
        self.d.bhkw._senden=Mock(side_effect=self.send)
        self.sent=[]
        self.d.automatik.aktiv.return_value=True
        self.wp=WPAutomatik(self.d)
    async def send(self, cmd): self.sent.append(cmd)
    async def asyncTearDown(self): self.temp.cleanup()
    async def test_stop_at_soc_cutoff(self):
        self.wp.an=True
        self.wp._sensoren=lambda: {'soc':89.5,'soc_insel':98.,'temp':21.,'sd':0.4}
        self.wp._trend=lambda *a: None
        await self.wp.tick()
        self.assertEqual(self.sent,['WP_S'])
        self.assertFalse(self.wp.an)
    async def test_stop_at_maximum(self):
        self.wp.an=True
        self.wp._sensoren=lambda: {'soc':97.,'soc_insel':98.,'temp':23.,'sd':0.4}
        self.wp._trend=lambda *a: None
        await self.wp.tick()
        self.assertEqual(self.sent,['WP_S'])
    async def test_no_start_without_fresh_sensors(self):
        self.wp._sensoren=lambda: None
        await self.wp.tick()
        self.assertEqual(self.sent,[])
    async def test_auto_off_stops_managed_wp(self):
        self.wp.an=True
        self.d.automatik.aktiv.return_value=False
        await self.wp.tick()
        self.assertEqual(self.sent,['WP_S'])
    async def test_restart_stops_prior_heating(self):
        self.wp.an=True
        from pv_cache import atomar_speichern
        atomar_speichern(self.wp.zustandspfad, {'wp_heizauftrag_offen': True})
        neu=WPAutomatik(self.d)
        await neu.tick()
        self.assertEqual(self.sent,['WP_S'])
    def test_soc_parameters(self):
        pruefen(replace(Parameter(),wp_soc_aus_prozent=90,wp_soc_ein_prozent=96))
        with self.assertRaises(ValueError):
            pruefen(replace(Parameter(),wp_soc_aus_prozent=97,wp_soc_ein_prozent=96))
    def test_bhkw_auto_no_timeout(self):
        self.d.manuell.aktiv.return_value=False
        auto=BHKWAutomatik(self.d)
        auto.ein()
        self.assertTrue(auto.aktiv())
        self.assertIsNone(auto.status()['freigabe_rest_s'])
        self.assertFalse(BHKWAutomatik(self.d).aktiv())
