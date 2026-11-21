"""Sicherheitsschicht: echte Schaltbefehle in Version 0.1 explizit gesperrt."""
class AktorenGesperrt(RuntimeError):
    pass


def sende_aktor_befehl(befehl: str):
    raise AktorenGesperrt(f'Kein Hardwarezugriff in der Simulation: {befehl}')
