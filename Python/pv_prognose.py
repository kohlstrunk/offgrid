"""Anzeige der PV-Tagesprognosen aus dem persistenten Cache.

ACHTUNG: Dieses Hilfsskript erzeugt keine HTTP-Anfragen. Ausschliesslich
pv_cache.PVCache.faellig() im laufenden dienst.py darf das API-Tagesbudget
verbrauchen. Direkter Import des alten pv.py wuerde dessen HTTP-Aufrufe
ungezaehlt ausfuehren und soll daher vermieden werden.
"""
from datetime import datetime, timedelta
from pathlib import Path
import json
from zoneinfo import ZoneInfo
from solar_waerme import FENSTER_FELD, fenster_waerme_tage


def prognosen_morgen(cache_datei='daten/pv_cache.json'):
    morgen = (datetime.now(ZoneInfo('Europe/Berlin')) + timedelta(days=1)).date()
    try:
        state = json.loads(Path(cache_datei).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError, OSError):
        state = {}
    prognosen = state.get('prognosen_kwh', {})
    tag = morgen.isoformat()
    fenster_werte = prognosen.get(FENSTER_FELD, {})
    proxy = fenster_werte.get(tag)
    return {
        'datum': tag,
        'pv1_4kwp_38grad_kwh': prognosen.get('pv1_4kwp_38grad', {}).get(tag),
        'pv2_2_5kwp_20grad_kwh': prognosen.get('pv2_2_5kwp_20grad', {}).get(tag),
        'fenster_sued_2_2kwp_90grad_kwh': proxy,
        'fenster_waerme_kwh_th': None if proxy is None else round(3.0 * float(proxy), 3),
        'quelle': 'Lokaler PV-Cache (keine zusaetzlichen API-Anfragen)'
    }


if __name__ == '__main__':
    print(json.dumps(prognosen_morgen(), ensure_ascii=False, indent=2))
