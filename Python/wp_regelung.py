"""Thermostatische WP-Freigabe; elektrische Verfügbarkeit wird separat geprüft."""
from parameter import Parameter


def modus(innen_c: float, bhkw_an: bool, p: Parameter) -> str:
    # Bei BHKW-Wärme zunächst keine zusätzliche Heizwärme erzeugen.
    if innen_c < p.minimum_c and not bhkw_an:
        return 'HEIZEN'
    if innen_c > p.maximum_c:
        return 'KUEHLEN'
    return 'STOP'
