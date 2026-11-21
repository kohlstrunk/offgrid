"""Regression: local STATUS forwards WP state to bhkw_lokal.py."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from dienst import Dienst


class LocalWpStatus105(unittest.IsolatedAsyncioTestCase):
    async def test_local_status_includes_wp(self):
        fake = SimpleNamespace(
            automatik=SimpleNamespace(status=lambda: {'aktiv': True}),
            bhkw=SimpleNamespace(status=lambda: {'zustand': 'GESPERRT'},
                                      lokal=AsyncMock(return_value={})),
            wp_auto=SimpleNamespace(status=lambda: {
                'wp_modus': 'KUEHLEN',
                'wp_heizung_beauftragt': False,
                'wp_kuehlung_beauftragt': True}),
        )
        reader = asyncio.StreamReader()
        reader.feed_data(b'STATUS\n')
        reader.feed_eof()
        writer = Mock()
        writer.write = Mock()
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        await Dienst.lokaler_aktor_client(fake, reader, writer)
        response = json.loads(writer.write.call_args.args[0].decode('utf-8'))
        self.assertTrue(response['ok'])
        self.assertEqual(response['wp_automatik']['wp_modus'], 'KUEHLEN')
        self.assertTrue(response['wp_automatik']['wp_kuehlung_beauftragt'])
