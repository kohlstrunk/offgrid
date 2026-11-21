"""Nur direkt auf dem Raspberry Pi ausfuehren; kein Zugriff ueber RE_/Bridge."""
import argparse
import asyncio
import json
from pathlib import Path

async def senden(pfad, kommando):
    reader, writer = await asyncio.open_unix_connection(str(pfad))
    try:
        writer.write((kommando + '\n').encode('ascii'))
        await writer.drain()
        antwort = await asyncio.wait_for(reader.readline(), timeout=65)
        if not antwort:
            raise RuntimeError('Keine Antwort')
        return json.loads(antwort)
    finally:
        writer.close()
        await writer.wait_closed()

def main():
    p = argparse.ArgumentParser(description='BHKW-Lokalbedienung 0.9.9, nicht fernbedienbar')
    p.add_argument('aktion', choices=('STATUS','FREIGEBEN','START','STOP','SPERREN','BESTAETIGEN','AUTO_EIN','AUTO_AUS'))
    p.add_argument('--datenordner', default='daten')
    args = p.parse_args()
    antwort = asyncio.run(senden(Path(args.datenordner) / 'bhkw_lokal.sock', args.aktion))
    if antwort.get('ok') is True:
        print(json.dumps(antwort, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(antwort, indent=2, ensure_ascii=False))
    if antwort.get('ok') is False:
        p.exit(1, 'Aktion abgelehnt: %s\n' % antwort.get('fehler', 'Unbekannter Fehler'))

if __name__ == '__main__':
    main()
