import asyncio
import unittest
from dienst import Dienst


class FakeWriter:
    def __init__(self):
        self.commands = []
    def write(self, data):
        self.commands.append(data)
    async def drain(self):
        pass


class DienstTests(unittest.IsolatedAsyncioTestCase):
    async def test_abfrage_und_passives_mithoeren(self):
        d = Dienst(datenordner='/tmp/offgrid-test-dienst-no-pv', intervall=600, funkpause=0, timeout=0.1)
        d.writer = FakeWriter()
        task = asyncio.create_task(d.abfragen())
        try:
            await asyncio.sleep(1.1)
            self.assertEqual(d.writer.commands, [b'TM_B\n'])
            self.assertEqual(d.cache.aufnehmen('IT 22.1 C AT 4.2 C'), 'TM_B')
            d.ausstehend = None
            await asyncio.sleep(1.1)
            self.assertEqual(d.writer.commands[-1], b'IN_W\n')
            self.assertNotIn(b'TM_B\n', d.writer.commands[1:])
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
