"""Simulationsergebnisse als JSON speichern."""
import json
from dataclasses import asdict
from pathlib import Path


def speichern(pfad: str, ergebnis):
    Path(pfad).write_text(json.dumps(asdict(ergebnis), indent=2,
                                    ensure_ascii=False), encoding='utf-8')
