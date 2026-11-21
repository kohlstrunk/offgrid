"""Annahmen für die erste, ausschließlich simulierte Version."""
from dataclasses import dataclass

STARTPLAENE = {
    0: (),
    1: (4.0,),
    2: (4.0, 17.0),
    3: (4.0, 12.0, 17.0),
    4: (4.0, 9.0, 14.0, 20.0),
    5: (4.0, 9.0, 14.0, 19.0, 23 + 20 / 60),
}

@dataclass(frozen=True)
class Parameter:
    solltemperatur_c: float = 22.0
    minimum_c: float = 21.0
    maximum_c: float = 23.0
    hauskapazitaet_kwh_pro_k: float = 15.0  # geschätzt, nicht vermessen
    waermeverlust_kw_pro_k: float = 0.1
    bhkw_elektrisch_kw: float = 0.75
    bhkw_thermisch_kw: float = 3.1
    bhkw_laufzeit_h: float = 4.0
    bhkw_sperrzeit_h: float = 20 / 60  # technische Neustartfreigabe; Reinigung separat prüfen
    wp_elektrisch_kw: float = 1.0
    wp_heizen_kw: float = 3.0  # vereinfacht: unabhängig von Außentemperatur
    wp_kuehlen_kw: float = 3.0  # vorläufige Annahme
    batterie1_kwh: float = 20.0
    batterie2_kwh: float = 7.0
    einspeiser_kw: float = 0.8
    plan_gesamtreserve_kwh: float = 5.4
    plan_inselbatterie_min_kwh: float = 1.5
    wp_soc_aus_prozent: float = 90.0  # Abschaltgrenze fuer PV-Ueberschussheizen
    wp_soc_ein_prozent: float = 96.0  # Gesamt-SOC und Insel-SOC nahe voll
    wp_hysterese_c: float = 0.2
    plan_wp_reserve_zuschlag_kwh: float = 0.5
    basislast_kw: float = 0.25  # unbekannt: nur Simulationsannahme
    schritt_min: int = 5
    sensor_max_alter_s: int = 600  # zugleich Abfragegrenze und Gueltigkeit
    min_funkpause_s: int = 5

@dataclass(frozen=True)
class Anfangszustand:
    innen_c: float = 22.0
    batterie1_kwh: float = 12.0
    batterie2_kwh: float = 4.0
    # Auf 5 setzen, falls das Vortagsschema einen BHKW-Start um 23:20 enthält.
    starts_vortag: int = 0

# Bedienbare Parameter. Technische Konstanten bleiben im Programm fest.
AENDERBAR = {
    'solltemperatur_c', 'minimum_c', 'maximum_c',
    'hauskapazitaet_kwh_pro_k', 'waermeverlust_kw_pro_k',
    'basislast_kw', 'plan_gesamtreserve_kwh',
    'plan_inselbatterie_min_kwh', 'wp_hysterese_c',
    'plan_wp_reserve_zuschlag_kwh', 'wp_soc_aus_prozent', 'wp_soc_ein_prozent',
    'wp_elektrisch_kw', 'wp_heizen_kw', 'wp_kuehlen_kw',
    'sensor_max_alter_s', 'min_funkpause_s'
}

def pruefen(p):
    """Plausibilitaetspruefung, bevor geaenderte Parameter wirksam werden."""
    from math import isfinite
    from dataclasses import asdict
    d = asdict(p)
    if any(not isfinite(float(v)) for v in d.values()):
        raise ValueError('Parameter muessen endlich sein')
    if not (10 <= p.minimum_c < p.solltemperatur_c < p.maximum_c <= 35):
        raise ValueError('Temperaturen: 10 <= minimum < soll < maximum <= 35')
    if not (50 <= p.wp_soc_aus_prozent < p.wp_soc_ein_prozent <= 100):
        raise ValueError('WP-SOC: 50 <= aus < ein <= 100')
    if p.wp_hysterese_c < 0 or p.wp_hysterese_c >= p.solltemperatur_c - p.minimum_c:
        raise ValueError('WP-Hysterese ungueltig')
    if not (0 < p.hauskapazitaet_kwh_pro_k <= 200):
        raise ValueError('Hauskapazitaet muss zwischen 0 und 200 kWh/K liegen')
    if not (0 < p.waermeverlust_kw_pro_k <= 5):
        raise ValueError('Waermeverlustkoeffizient ungueltig')
    if type(p.sensor_max_alter_s) is not int or not (60 <= p.sensor_max_alter_s <= 86400):
        raise ValueError('sensor_max_alter_s muss ganze Sekunden zwischen 60 und 86400 sein')
    if type(p.min_funkpause_s) is not int or not (0 <= p.min_funkpause_s <= 300):
        raise ValueError('min_funkpause_s muss ganze Sekunden zwischen 0 und 300 sein')
    if not (0 <= p.basislast_kw <= 10):
        raise ValueError('Basislast ungueltig')
    if not (0 <= p.plan_gesamtreserve_kwh <= p.batterie1_kwh + p.batterie2_kwh):
        raise ValueError('Gesamtreserve ausserhalb der Batteriekapazitaet')
    if not (0 <= p.plan_inselbatterie_min_kwh <= p.batterie1_kwh):
        raise ValueError('Inselbatterie-Reserve ausserhalb der Batteriekapazitaet')
    if not (0 <= p.plan_wp_reserve_zuschlag_kwh <= 10):
        raise ValueError('WP-Reservezuschlag ungueltig')
    if any(getattr(p, n) <= 0 or getattr(p, n) > 20 for n in ('wp_elektrisch_kw','wp_heizen_kw','wp_kuehlen_kw')):
        raise ValueError('WP-Leistung ausserhalb 0..20 kW')
    return p

def lade_parameter(pfad):
    """Laedt nur explizit aenderbare Parameter und prueft alle Werte."""
    import json
    from pathlib import Path
    pfad = Path(pfad)
    if not pfad.exists():
        return Parameter()
    data = json.loads(pfad.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) - AENDERBAR:
        raise ValueError('Unbekannte Parameter in ' + str(pfad))
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in data.values()):
        raise ValueError('Parameter muessen numerisch sein')
    from dataclasses import replace
    return pruefen(replace(Parameter(), **data))

def speichere_parameter(pfad, p):
    """Atomare persistente Speicherung; gleicher Mechanismus wie PV-Cache."""
    from dataclasses import asdict
    from pv_cache import atomar_speichern
    pruefen(p)
    atomar_speichern(pfad, {k: asdict(p)[k] for k in sorted(AENDERBAR)})
