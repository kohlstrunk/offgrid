"""Integration der lokalen RE-Befehlsschnittstelle, ohne Hardware."""
import asyncio
import json
import tempfile
import unittest

from dienst import Dienst

class KurzbefehleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dienst = Dienst(datenordner=self.tmp.name)
        self.server = await asyncio.start_server(self.dienst.status_client, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    async def request(self, command):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write((command+'\n').encode())
        await writer.drain()
        res = json.loads(await asyncio.wait_for(reader.readline(), 4))
        writer.close()
        await writer.wait_closed()
        return res

    async def test_aliases_and_mode(self):
        self.assertEqual(self.dienst.betriebsmodus, 'SIMULATION')
        x = await self.request('RE_P3')
        self.assertEqual(x['manuelle_vorgabe']['schema'], 3)
        x = await self.request('RE_START')
        self.assertEqual(self.dienst.betriebsmodus, 'AKTIV')
        self.assertFalse(x['aktorfreigabe'])
        x = await self.request('RE_STOP')
        self.assertEqual(x['betriebsmodus'], 'SIMULATION')
        x = await self.request('RE_PA')
        self.assertIsNone(self.dienst.manuell.aktiv())
        x = await self.request('RE_P0')
        self.assertEqual(x['manuelle_vorgabe']['schema'], 0)

    async def test_reboot_defaults_simulation(self):
        await self.request('RE_START')
        other = Dienst(datenordner=self.tmp.name)
        self.assertEqual(other.betriebsmodus, 'SIMULATION')
