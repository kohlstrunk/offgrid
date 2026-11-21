"""Echte Sensordaten LESEN, niemals Aktoren steuern.

Beispiel: python3 monitor.py --host 127.0.0.1
"""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime
import json
from kommunikation import BridgeClient

async def lesen(host, port, timeout):
    async with BridgeClient(host, port, timeout) as client:
        tm, insel, batterie = await client.messungen()
        return {
            'zeit': datetime.now().astimezone().isoformat(),
            'temperatur': asdict(tm), 'inselwechselrichter': asdict(insel),
            'batteriemanager': asdict(batterie),
            'hinweis': 'SD und En sind entnommene Energiemengen, KEINE Restkapazitäten.'
        }

def main():
    p = argparse.ArgumentParser(description='Offgrid-Sensormonitor (nur lesend)')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=1234)
    p.add_argument('--timeout', type=float, default=5)
    args = p.parse_args()
    try:
        print(json.dumps(asyncio.run(lesen(args.host, args.port, args.timeout)), indent=2, ensure_ascii=False))
    except (OSError, ValueError, TimeoutError, ConnectionError) as e:
        p.exit(1, 'Messung fehlgeschlagen: %s\n' % e)

if __name__ == '__main__':
    main()
