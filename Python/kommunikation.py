"""Lesender TCP-Client für die byte-transparente Bridge.

Die Bridge sendet seriell empfangene Daten an ALLE TCP-Clients, und kein
Echo der vom jeweiligen TCP-Client gesendeten Befehle. Antworten sind nicht
mit einer Anfrage-ID versehen; daher sequenziell abfragen und niemals als
transaktional garantiert ansehen. Andere Clients können dazwischenfunken.
"""
import asyncio
from sensoren import parse_tm_b, parse_in_w, parse_bm_w, parse_mo_w

class BridgeClient:
    def __init__(self, host='127.0.0.1', port=1234, timeout=5):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = None
        self.writer = None

    async def __aenter__(self):
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout)
        return self

    async def __aexit__(self, *_):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def anfrage(self, befehl, parser, marker):
        if befehl not in ('TM_B', 'IN_W', 'BM_W', 'MO_W'):
            raise ValueError('Nur freigegebene Leseabfragen erlaubt')
        # Es ist kein Request-ID-Protokoll vorhanden; alte oder fremde
        # Antworten können nicht absolut sicher ausgeschlossen werden.
        self.writer.write((befehl + '\n').encode('ascii'))
        await self.writer.drain()
        loop = asyncio.get_running_loop()
        ende = loop.time() + self.timeout
        while True:
            remaining = ende - loop.time()
            if remaining <= 0:
                raise TimeoutError('Keine passende Antwort auf ' + befehl)
            line = await asyncio.wait_for(self.reader.readline(), timeout=remaining)
            if not line:
                raise ConnectionError('TCP-Bridge hat Verbindung geschlossen')
            antwort = line.decode('utf-8', errors='replace').strip()
            if marker not in antwort:
                continue
            try:
                return parser(antwort)
            except ValueError:
                continue

    async def messungen(self):
        tm = await self.anfrage('TM_B', parse_tm_b, 'IT ')
        insel = await self.anfrage('IN_W', parse_in_w, 'SD ')
        batterie = await self.anfrage('BM_W', parse_bm_w, 'Ch:')
        return tm, insel, batterie
