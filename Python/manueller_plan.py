"""Zeitlich begrenzte, persistente manuelle BHKW-Schemawahl (nur Simulation)."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from pv_cache import atomar_speichern
from parameter import STARTPLAENE

class ManuellerPlan:
    def __init__(self, pfad):
        self.pfad = Path(pfad)
        self._plan = None
        self.laden()

    def laden(self):
        try:
            obj = json.loads(self.pfad.read_text(encoding='utf-8'))
            n = obj['schema']
            start = datetime.fromisoformat(obj['gesetzt_utc'])
            ende = datetime.fromisoformat(obj['gueltig_bis_utc'])
            if (type(n) is int and n in STARTPLAENE and start.tzinfo is not None
                    and ende.tzinfo is not None and ende > start):
                self._plan = obj
        except (OSError, ValueError, TypeError, KeyError):
            self._plan = None

    def aktiv(self, jetzt=None):
        jetzt = jetzt or datetime.now(timezone.utc)
        if self._plan and datetime.fromisoformat(self._plan['gueltig_bis_utc']) > jetzt:
            return dict(self._plan)
        return None

    def setzen(self, schema, jetzt=None):
        if type(schema) is not int or schema not in STARTPLAENE:
            raise ValueError('Schema muss eine ganze Zahl von 0 bis 5 sein')
        jetzt = (jetzt or datetime.now(timezone.utc)).astimezone(timezone.utc)
        ende = jetzt + timedelta(hours=24)
        obj = {'schema': schema, 'gesetzt_utc': jetzt.isoformat(),
               'gueltig_bis_utc': ende.isoformat(), 'modus': 'MANUELL, NUR SIMULATION'}
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        atomar_speichern(self.pfad, obj)
        self._plan = obj
        return dict(obj)

    def aufheben(self):
        self._plan = None
        try:
            self.pfad.unlink()
        except FileNotFoundError:
            pass
