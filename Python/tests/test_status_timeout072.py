import asyncio
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from dienst import Dienst
from status import lesen


class StatusTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_slow_plan_does_not_block_event_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            dienst = Dienst(datenordner=tmp)
            def slow_plan():
                time.sleep(0.2)
                return {'zeitpunkt': 'test', 'status': 'TEST', 'szenarien': []}
            with patch.object(dienst, 'plan', side_effect=slow_plan):
                server = await asyncio.start_server(dienst.status_client, '127.0.0.1', 0)
                port = server.sockets[0].getsockname()[1]
                try:
                    pending = asyncio.create_task(lesen('RE_PLAN', port, timeout=2))
                    await asyncio.sleep(0.03)
                    before = time.monotonic()
                    quick = await asyncio.wait_for(lesen('RE_P', port, timeout=2), timeout=0.15)
                    self.assertIn('sensor_max_alter_s', quick)
                    self.assertLess(time.monotonic() - before, 0.15)
                    data = await pending
                    self.assertEqual(data['status'], 'TEST')
                finally:
                    server.close()
                    await server.wait_closed()

    async def test_missing_response_has_readable_error(self):
        async def close(reader, writer):
            await reader.readline()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(close, '127.0.0.1', 0)
        try:
            port = server.sockets[0].getsockname()[1]
            with self.assertRaisesRegex(RuntimeError, 'ohne Antwort'):
                await lesen('RE_PLAN', port, timeout=1)
        finally:
            server.close()
            await server.wait_closed()
