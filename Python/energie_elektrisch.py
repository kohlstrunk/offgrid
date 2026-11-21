"""Vereinfachtes Zwei-Batterien-Modell ohne unbekannte Wechselrichterwirkungsgrade."""
from dataclasses import dataclass
from parameter import Parameter

@dataclass
class Speicher:
    eins_kwh: float
    zwei_kwh: float


def schritt(s: Speicher, pv1_kw: float, pv2_kw: float, bhkw_kw: float,
            verbrauch_kw: float, dauer_h: float, p: Parameter):
    """Simuliert Leistungsaustausch; die reale spannungsgeführte Einspeisung
    ist NICHT bekannt und wird als vereinfachte bedarfsabhängige Einspeisung
    mit maximal 800 W approximiert.
    """
    # Batterie 2: PV2 und BHKW laden, Einspeisung maximal 800 W ins Inselnetz.
    deficit_kw = max(0.0, verbrauch_kw - pv1_kw)
    transfer_kw = min(p.einspeiser_kw, deficit_kw,
                      max(0.0, s.zwei_kwh / dauer_h + pv2_kw + bhkw_kw))
    roh2 = s.zwei_kwh + (pv2_kw + bhkw_kw - transfer_kw) * dauer_h
    roh1 = s.eins_kwh + (pv1_kw + transfer_kw - verbrauch_kw) * dauer_h
    fehlenergie_kwh = max(0.0, -roh1) + max(0.0, -roh2)
    abgeregelt_kwh = max(0.0, roh1 - p.batterie1_kwh) + max(0.0, roh2 - p.batterie2_kwh)
    s.eins_kwh = max(0.0, min(p.batterie1_kwh, roh1))
    s.zwei_kwh = max(0.0, min(p.batterie2_kwh, roh2))
    return fehlenergie_kwh, abgeregelt_kwh, transfer_kw


def schritt_mit_bhkw_abschaltung(s: Speicher, pv1_kw: float, pv2_kw: float, bhkw_kw: float,
                                  verbrauch_kw: float, dauer_h: float, p: Parameter):
    """Planungsmodell: PV wird bei vollen Speichern abgeregelt.

    BHKW-Leistung wird NICHT als abregelbar angesehen. Falls selbst bei
    vollstaendiger Abregelung der PV2 die kleine Batterie durch das BHKW
    ueberlaufen wuerde, endet der BHKW-Lauf modellhaft in diesem Zeitschritt.
    Das ersetzt KEINE 53-V-Spannungsprognose, da keine U(SOC)-Kennlinie vorliegt.

    Rueckgabe: (Fehlenergie, abgeregelte_PV_kWh, Transfer_kW,
                BHKW_abschaltung, BHKW_Wirkanteil).
    """
    if dauer_h <= 0:
        raise ValueError('dauer_h muss positiv sein')
    # Die kleine Batterie speist nur bis 800 W und nur bei Bedarf ein.
    deficit_kw = max(0.0, verbrauch_kw - pv1_kw)
    transfer_kw = min(p.einspeiser_kw, deficit_kw,
                      max(0.0, s.zwei_kwh / dauer_h + pv2_kw + bhkw_kw))

    # Der BHKW-Beitrag hat Vorrang vor PV2: bei vollem Akku wird PV2
    # abgeregelt, BHKW kann nur durch automatisches Abschalten begrenzt werden.
    max_ladung_kw = max(0.0, (p.batterie2_kwh - s.zwei_kwh) / dauer_h)
    verfuegbar_kw = max_ladung_kw + transfer_kw
    effektives_bhkw_kw = min(bhkw_kw, verfuegbar_kw)
    auto_stop = bhkw_kw > verfuegbar_kw + 1e-9
    pv2_nutzbar_kw = min(pv2_kw, max(0.0, verfuegbar_kw - effektives_bhkw_kw))
    pv2_abregelung = (pv2_kw - pv2_nutzbar_kw) * dauer_h
    roh2 = s.zwei_kwh + (pv2_nutzbar_kw + effektives_bhkw_kw - transfer_kw) * dauer_h
    roh1 = s.eins_kwh + (pv1_kw + transfer_kw - verbrauch_kw) * dauer_h
    fehlenergie = max(0.0, -roh1) + max(0.0, -roh2)
    pv1_abregelung = max(0.0, roh1 - p.batterie1_kwh)
    s.eins_kwh = max(0.0, min(p.batterie1_kwh, roh1))
    s.zwei_kwh = max(0.0, min(p.batterie2_kwh, roh2))
    anteil = effektives_bhkw_kw / bhkw_kw if bhkw_kw > 0 else 0.0
    return fehlenergie, pv1_abregelung + pv2_abregelung, transfer_kw, auto_stop, anteil
