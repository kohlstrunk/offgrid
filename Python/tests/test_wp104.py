import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from dataclasses import replace
from parameter import Parameter
from wp_automatik import WPAutomatik
from pv_cache import atomar_speichern

class WP104Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Mock()
        self.d.datenordner = Path(self.tmp.name)
        self.d.parameter = replace(Parameter(), solltemperatur_c=23, maximum_c=24)
        self.d.intervall = 600
        self.d.automatik.aktiv.return_value = True
        self.d.bhkw._bridge_bereit.return_value = True
        self.d.cache.motorstatus.return_value = {'zustand':'AUS_BEREIT'}
        self.sent = []
        async def sending(cmd): self.sent.append(cmd)
        self.d.bhkw._senden.side_effect = sending
        self.wp = WPAutomatik(self.d)
        self.wp._trend=lambda *a: None
        self.sensor = {'soc':96,'soc_insel':97,'temp':25,'sd':0.5}
        self.wp._sensoren=lambda: self.sensor

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_cooling_starts_only_above_maximum(self):
        self.sensor['temp']=24
        # Prevent expensive forecast in the heating branch.
        self.wp.wp_heute_beendet = __import__('datetime').datetime.now(__import__('zoneinfo').ZoneInfo('Europe/Berlin')).date().isoformat()
        await self.wp.tick()
        self.assertEqual(self.sent, [])
        self.sensor['temp']=24.01
        await self.wp.tick()
        self.assertEqual(self.sent, ['WP_C'])
        self.assertEqual(self.wp.status()['wp_modus'],'KUEHLEN')

    async def test_cooling_hysteresis(self):
        self.sensor['temp']=25
        await self.wp.tick()
        self.sensor['temp']=23.0
        await self.wp.tick()
        self.assertEqual(self.sent, ['WP_C'])
        self.sensor['temp']=22.99
        await self.wp.tick()
        self.assertEqual(self.sent, ['WP_C','WP_S'])

    async def test_bhkw_blocks_cooling_and_stops_active_cooling(self):
        self.d.cache.motorstatus.return_value={'zustand':'LAEUFT'}
        await self.wp.tick()
        self.assertEqual(self.sent, [])
        self.d.cache.motorstatus.return_value={'zustand':'AUS_BEREIT'}
        await self.wp.tick()
        self.d.cache.motorstatus.return_value={'zustand':'LAEUFT'}
        await self.wp.tick()
        self.assertEqual(self.sent, ['WP_C', 'WP_S'])

    async def test_heating_not_stopped_by_bhkw(self):
        self.wp.an=True
        self.wp.modus='HEIZEN'
        self.sensor['temp']=22
        self.d.cache.motorstatus.return_value={'zustand':'LAEUFT'}
        from unittest.mock import patch
        from datetime import datetime
        from zoneinfo import ZoneInfo
        with patch('wp_automatik.datetime', wraps=datetime) as fake_dt:
            fake_dt.now.return_value = datetime(2026, 6, 20, 12, 0, tzinfo=ZoneInfo('Europe/Berlin'))
            await self.wp.tick()
        self.assertEqual(self.sent, [])
        self.assertTrue(self.wp.an)

    async def test_cooling_restart_cleanup(self):
        atomar_speichern(self.wp.zustandspfad, {'wp_modus':'KUEHLEN','wp_heizauftrag_offen':True})
        new = WPAutomatik(self.d)
        await new.tick()
        self.assertEqual(self.sent,['WP_S'])
        self.assertEqual(new.modus,'AUS')

    async def test_auto_off_stops_cooling(self):
        self.sensor['temp']=25
        await self.wp.tick()
        self.d.automatik.aktiv.return_value=False
        await self.wp.tick()
        self.assertEqual(self.sent,['WP_C','WP_S'])

    async def test_unknown_motor_does_not_start_cooling(self):
        self.d.cache.motorstatus.return_value={'zustand':'UNBEKANNT'}
        await self.wp.tick()
        self.assertEqual(self.sent,[])

if __name__=='__main__': unittest.main()
