"""Persistenter, zeitlich begrenzter Planungs-Boost (keine Aktorsteuerung)."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
import json
from pv_cache import atomar_speichern

class EnergieBoost:
    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self._data = None
        self.laden()

    def laden(self):
        try:
            data = json.loads(self.pfad.read_text(encoding='utf-8'))
            start = datetime.fromisoformat(data['start_utc'])
            ende = datetime.fromisoformat(data['ende_utc'])
            kwh = float(data['energie_kwh'])
            if (start.tzinfo is not None and ende.tzinfo is not None and
                    ende > start and 0 < kwh <= 27 and isfinite(kwh)):
                self._data = data
            else:
                self._data = None
        except (OSError, KeyError, TypeError, ValueError):
            self._data = None

    def setzen(self, kwh, jetzt=None):
        kwh = float(kwh)
        if not isfinite(kwh) or not 0 <= kwh <= 27:
            raise ValueError('Boost muss zwischen 0 und 27 kWh liegen')
        if kwh == 0:
            self.aufheben()
            return None
        jetzt = (jetzt or datetime.now(timezone.utc)).astimezone(timezone.utc)
        # Idempotenz: derselbe Boost darf weder erneut angerechnet werden
        # noch sein Ablaufdatum durch wiederholte Befehle verschieben.
        aktiv = self.aktiv(jetzt)
        if aktiv is not None and abs(float(aktiv['energie_kwh']) - kwh) < 1e-9:
            return aktiv
        obj = {'energie_kwh': kwh, 'start_utc': jetzt.isoformat(),
               'ende_utc': (jetzt + timedelta(hours=24)).isoformat()}
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        atomar_speichern(self.pfad, obj)
        self._data = obj
        return self.aktiv(jetzt)

    def aktiv(self, jetzt=None):
        jetzt = (jetzt or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self._data:
            return None
        start = datetime.fromisoformat(self._data['start_utc'])
        ende = datetime.fromisoformat(self._data['ende_utc'])
        if jetzt >= ende:
            return None
        rate = self._data['energie_kwh'] / ((ende-start).total_seconds()/3600)
        rest = max(0.0, (ende - max(jetzt, start)).total_seconds()/3600) * rate
        return dict(self._data, rest_kwh=round(rest, 3), leistung_kw=rate)

    def aufheben(self):
        self._data = None
        try:
            self.pfad.unlink()
        except FileNotFoundError:
            pass


def boost_kwh_im_intervall(boost, beginn, ende):
    """Rechne nur die Restdauer des Boosts als elektrische Zusatzlast."""
    if not boost:
        return 0.0
    start = datetime.fromisoformat(boost['start_utc'])
    stop = datetime.fromisoformat(boost['ende_utc'])
    a = max(beginn, start)
    b = min(ende, stop)
    if b <= a:
        return 0.0
    return (b-a).total_seconds()/3600 * boost['leistung_kw']
