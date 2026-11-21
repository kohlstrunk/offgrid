"""Persistenter Forecast.solar-Cache mit lokalem Tagesbudget (Europe/Berlin).

Eine Anfrage = ein Solarfeld (2 echte + 1 fiktives). Zeitfenster 00:15/12:15; pro Fenster
höchstens ein Durchgang, auch bei HTTP-Fehlern, um Funk/HTTP zu schonen.
"""
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo('Europe/Berlin')
FELDER = {'pv1_4kwp_38grad': (4.0, 38),
          'pv2_2_5kwp_20grad': (2.5, 20),
          'fenster_sued_2_2kwp_90grad': (2.2, 90)}


def atomar_speichern(pfad, daten):
    pfad = Path(pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + pfad.name, dir=str(pfad.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(daten, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, pfad)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def api_abruf(kwp, neigung, lat=51.24, lon=12.72):
    import requests
    url = f'https://api.forecast.solar/estimate/{lat}/{lon}/{neigung}/0/{kwp}'
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    result = r.json()['result']
    return {
        'tage': {tag: round(float(wh) / 1000, 3)
                 for tag, wh in result['watt_hours_day'].items()},
        # forecast.solar: kumulierte Wh je Kalendertag, Zeitstempel lokal.
        # Kein zusätzlicher HTTP-Abruf erforderlich.
        'kumuliert_wh': {tag: float(wh)
                         for tag, wh in result.get('watt_hours', {}).items()}
    }


class PVCache:
    def __init__(self, datei, limit=12, abruf=api_abruf, jetzt=None):
        self.datei = Path(datei)
        self.limit = min(max(0, int(limit)), 12)
        self.abruf = abruf
        self.jetzt = jetzt or (lambda: datetime.now(BERLIN))
        try:
            self.state = json.loads(self.datei.read_text(encoding='utf-8'))
            if not isinstance(self.state, dict):
                raise ValueError('Kein Dictionary')
        except (FileNotFoundError, ValueError, OSError):
            self.state = {}
        self._lock = asyncio.Lock()

    def _tag(self, jetzt):
        tag = jetzt.date().isoformat()
        if self.state.get('budget_tag') != tag:
            self.state['budget_tag'] = tag
            self.state['verbrauch'] = 0
            self.state['fenster'] = []
            atomar_speichern(self.datei, self.state)
        return tag

    def status(self):
        from solar_waerme import fenster_waerme_tage
        jetzt = self.jetzt().astimezone(BERLIN)
        waerme = fenster_waerme_tage(self.state.get('prognosen_kwh', {}), jetzt.date())
        from solar_waerme import fenster_waerme_halbstunden
        verlauf = fenster_waerme_halbstunden(
            self.state.get('zeitverlauf_wh', {}).get('fenster_sued_2_2kwp_90grad', {}),
            self.state.get('prognosen_kwh', {}).get('fenster_sued_2_2kwp_90grad', {}),
            jetzt.date())
        return {'fenster_waerme': waerme, 'fenster_halbstunden': verlauf, 'budget_tag': self.state.get('budget_tag'),
                'anfragen_heute': self.state.get('verbrauch', 0),
                'limit': self.limit, 'fenster': self.state.get('fenster', []),
                'abgerufen': self.state.get('abgerufen'),
                'prognosen_kwh': self.state.get('prognosen_kwh', {}),
                'letzter_fehler': self.state.get('letzter_fehler'),
                'letzter_protokollfehler': self.state.get('letzter_protokollfehler')}

    def _fehlende_felder(self, jetzt):
        """Felder ohne vollständige Prognose für heute UND morgen."""
        daten = self.state.get('prognosen_kwh', {})
        tage = (jetzt.date().isoformat(), (jetzt.date() + timedelta(days=1)).isoformat())
        return [name for name in FELDER
                if not isinstance(daten.get(name), dict)
                or any(daten[name].get(tag) is None for tag in tage)]

    def _protokolliere(self, eintrag):
        """Append-only JSONL. Fehler hier dürfen den Prognoseabruf nicht abbrechen."""
        pfad = self.datei.with_name('pv_prognosen.jsonl')
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open('a', encoding='utf-8') as f:
            f.write(json.dumps(eintrag, ensure_ascii=False, sort_keys=True) + '\n')

    async def _abrufen(self, jetzt, felder, anlass):
        """Pro Feld nur eine Anfrage, Zähler immer VOR der HTTP-Anfrage sichern."""
        anzahl = 0
        for name in felder:
            if self.state['verbrauch'] >= self.limit:
                break
            kwp, neigung = FELDER[name]
            self.state['verbrauch'] += 1
            atomar_speichern(self.datei, self.state)
            anzahl += 1
            try:
                werte = await asyncio.to_thread(self.abruf, kwp, neigung)
                if not isinstance(werte, dict) or not werte:
                    raise ValueError('Leere PV-Prognose')
                if 'tage' in werte:
                    tage = werte['tage']
                    kumuliert = werte.get('kumuliert_wh', {})
                else:
                    tage, kumuliert = werte, {}
                if not isinstance(tage, dict) or not tage:
                    raise ValueError('Keine Tagesprognose')
                self.state.setdefault('prognosen_kwh', {})[name] = tage
                self.state.setdefault('zeitverlauf_wh', {})[name] = kumuliert
                self.state['abgerufen'] = jetzt.isoformat()
                self.state['letzter_fehler'] = None
                protokoll = {'zeit': jetzt.isoformat(), 'anlass': anlass,
                             'feld': name, 'prognosen_kwh': tage,
                             'zeitverlauf_wh': kumuliert}
                try:
                    self._protokolliere(protokoll)
                except OSError as exc:
                    self.state['letzter_protokollfehler'] = str(exc)
            except Exception as exc:
                self.state['letzter_fehler'] = f'{name}: {type(exc).__name__}: {exc}'
            atomar_speichern(self.datei, self.state)
        return anzahl

    async def beim_start(self):
        """Nur fehlende Tagesprognosen für heute und morgen nachladen.

        Keine Wiederholung bereits vorhandener Felder. Keine unmittelbare
        Wiederholung bei API-Ausfällen. Das reguläre Abruffenster des aktuellen
        Halbtagsspots wird markiert, damit der Startabruf nicht direkt erneut
        durch einen planmäßigen Abruf gefolgt wird.
        """
        async with self._lock:
            jetzt = self.jetzt().astimezone(BERLIN)
            tag = self._tag(jetzt)
            fehlend = self._fehlende_felder(jetzt)
            if not fehlend or self.state['verbrauch'] >= self.limit:
                return False
            fenster = '12:15' if (jetzt.hour, jetzt.minute) >= (12, 15) else '00:15'
            kennung = tag + '/' + fenster
            # Auch vor 00:15 den 00:15-Slot als bedient markieren.
            if kennung not in self.state.setdefault('fenster', []):
                self.state['fenster'].append(kennung)
                atomar_speichern(self.datei, self.state)
            return bool(await self._abrufen(jetzt, fehlend, 'dienststart'))

    async def faellig(self):
        async with self._lock:
            jetzt = self.jetzt().astimezone(BERLIN)
            tag = self._tag(jetzt)
            fenster = '00:15' if (jetzt.hour, jetzt.minute) >= (0, 15) else None
            if (jetzt.hour, jetzt.minute) >= (12, 15):
                fenster = '12:15'
            if not fenster:
                return False
            kennung = tag + '/' + fenster
            if kennung in self.state.get('fenster', []):
                return False
            self.state.setdefault('fenster', []).append(kennung)
            atomar_speichern(self.datei, self.state)
            return bool(await self._abrufen(jetzt, list(FELDER), 'planmaessig'))
