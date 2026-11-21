"""Vergleich der sechs fixen BHKW-Schemata in einer 24-h-Simulation."""
from dataclasses import dataclass
from math import sin, pi
from parameter import Anfangszustand, Parameter, STARTPLAENE
from energie_elektrisch import Speicher, schritt as elektrisch_schritt
from energie_thermisch import temperatur_nach_schritt
from wp_regelung import modus

@dataclass(frozen=True)
class Prognose:
    aussen_c: float
    pv1_kwh: float  # Gesamtmenge für diesen Tag, Feld 4 kWp @ 38°
    pv2_kwh: float  # Gesamtmenge für diesen Tag, Feld 2,5 kWp @ 20°

@dataclass
class Ergebnis:
    starts: int
    zeiten: tuple
    ende_innen_c: float
    spitze_innen_c: float
    minimum_innen_c: float
    ende_batterie1_kwh: float
    ende_batterie2_kwh: float
    minimum_batterie1_kwh: float
    minimum_batterie2_kwh: float
    strommangel_kwh: float
    ueberschuss_kwh: float
    komfortabweichung_kh: float
    verlauf: list

    def zulaessig(self, p: Parameter) -> bool:
        return (self.strommangel_kwh < 1e-8
                and self.minimum_batterie1_kwh >= p.plan_inselbatterie_min_kwh
                and self.minimum_batterie1_kwh + self.minimum_batterie2_kwh >= p.plan_gesamtreserve_kwh
                and self.minimum_innen_c >= p.minimum_c
                and self.spitze_innen_c <= p.maximum_c)


def pv_leistung(stunde: float, tages_kwh: float) -> float:
    """Nur Ersatzprofil! Ohne Stundenprognose wird Tagesenergie sinusförmig verteilt."""
    if not (6 <= stunde < 18):
        return 0.0
    return tages_kwh * pi / 24 * sin(pi * (stunde - 6) / 12)


def laeuft(stunde: float, starts: int, starts_vortag: int, p: Parameter) -> bool:
    for start in STARTPLAENE[starts]:
        if start <= stunde < start + p.bhkw_laufzeit_h:
            return True
    for start in STARTPLAENE[starts_vortag]:
        if start - 24 <= stunde < start - 24 + p.bhkw_laufzeit_h:
            return True
    return False


def pruefe_startplan(zeiten: tuple, p: Parameter) -> bool:
    """Prüft 20-min-Neustartsperre. Bei 23:20 Start wird die
    Reinigung des 19-Uhr-Laufes bestimmungsgemäß übersprungen.
    Reinigungssteuerung bleibt Aufgabe der BHKW-Hardware.
    """
    starts = sorted(list(zeiten) + [z - 24 for z in zeiten] + [z + 24 for z in zeiten])
    return all(b - a >= p.bhkw_laufzeit_h + p.bhkw_sperrzeit_h - 1e-8
               for a, b in zip(starts, starts[1:]))


def simuliere(anzahl: int, anfang: Anfangszustand, prognose: Prognose,
               p: Parameter) -> Ergebnis:
    if anzahl not in STARTPLAENE:
        raise ValueError('Ungültige Startanzahl')
    if not pruefe_startplan(STARTPLAENE[anzahl], p):
        raise ValueError('Startplan verletzt BHKW-Sperrzeit')
    dt = p.schritt_min / 60
    speicher = Speicher(anfang.batterie1_kwh, anfang.batterie2_kwh)
    innen = anfang.innen_c
    tief_t = hoch_t = innen
    tief_1, tief_2 = speicher.eins_kwh, speicher.zwei_kwh
    mangel = abregelung = abweichung = 0.0
    verlauf = []
    for i in range(round(24 / dt)):
        h = i * dt
        an = laeuft(h, anzahl, anfang.starts_vortag, p)
        wp = modus(innen, an, p)
        pv1 = pv_leistung(h, prognose.pv1_kwh)
        pv2 = pv_leistung(h, prognose.pv2_kwh)
        # Kritischer WP-Betrieb ohne elektrische Reserve wird nicht freigegeben.
        if wp != 'STOP' and speicher.eins_kwh <= p.plan_inselbatterie_min_kwh:
            wp = 'STOP'
        verbrauch = p.basislast_kw + (p.wp_elektrisch_kw if wp != 'STOP' else 0)
        fehler, abgeregelt, einspeisung = elektrisch_schritt(
            speicher, pv1, pv2, p.bhkw_elektrisch_kw if an else 0,
            verbrauch, dt, p)
        mangel += fehler
        abregelung += abgeregelt
        innen = temperatur_nach_schritt(innen, prognose.aussen_c, an, wp, dt, p)
        tief_t, hoch_t = min(tief_t, innen), max(hoch_t, innen)
        tief_1 = min(tief_1, speicher.eins_kwh)
        tief_2 = min(tief_2, speicher.zwei_kwh)
        abweichung += max(0, p.minimum_c - innen, innen - p.maximum_c) * dt
        if i % round(60 / p.schritt_min) == 0:
            verlauf.append({'uhrzeit': f'{int(h):02}:{int((h%1)*60):02}',
                            'innen_c': round(innen, 3), 'bhkw': an,
                            'wp': wp, 'batterie1_kwh': round(speicher.eins_kwh, 3),
                            'batterie2_kwh': round(speicher.zwei_kwh, 3),
                            'einspeisung_kw': round(einspeisung, 3)})
    return Ergebnis(anzahl, STARTPLAENE[anzahl], innen, hoch_t, tief_t,
                    speicher.eins_kwh, speicher.zwei_kwh, tief_1, tief_2,
                    mangel, abregelung, abweichung, verlauf)


def waehle(anfang: Anfangszustand, prognose: Prognose, p: Parameter):
    ergebnisse = [simuliere(n, anfang, prognose, p) for n in STARTPLAENE]
    zulaessige = [e for e in ergebnisse if e.zulaessig(p)]
    if zulaessige:
        return min(zulaessige, key=lambda e: e.starts), ergebnisse, True
    # Ein ungeeigneter Plan DARF NICHT als ausführbarer Plan verwendet werden.
    # Für Diagnosezwecke wird nur die kleinste Verletzung ausgewiesen.
    diagnose = min(ergebnisse, key=lambda e: (
        1000 * e.strommangel_kwh + 100 * e.komfortabweichung_kh
        + 100 * max(0, p.plan_inselbatterie_min_kwh - e.minimum_batterie1_kwh)
        + 100 * max(0, p.plan_gesamtreserve_kwh - e.minimum_batterie1_kwh - e.minimum_batterie2_kwh)
        + e.starts))
    return diagnose, ergebnisse, False
