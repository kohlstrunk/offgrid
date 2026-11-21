"""Parser der tatsächlich beobachteten Geräteantworten. Keine Steuerbefehle.

SD und En bezeichnen ENTNOMMENE Energie seit Voll-Ladung; SoC = 100*(Kapazitaet - Entnahme)/Kapazitaet.
Pw und Pd sind abgegebene Leistungen. Ein Ladeleistungs-Vorzeichen ist
nicht spezifiziert und darf daraus nicht erfunden werden.
"""
from dataclasses import dataclass
from typing import Optional
import re

NUM = r'([+-]?\d+(?:[.,]\d+)?)'
def wert(s):
    return float(s.replace(',', '.'))

def feld(text, name, einheit=None):
    pattern = r'(?<!\w)' + re.escape(name) + r'\s*:?\s*' + NUM
    if einheit:
        pattern += r'\s*' + re.escape(einheit) + r'(?!\w)'
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        raise ValueError('Feld %s fehlt in Geräteantwort' % name)
    return wert(m.group(1))

@dataclass(frozen=True)
class Temperatur:
    innen_c: float
    aussen_c: float

@dataclass(frozen=True)
class Inselwechselrichter:
    entnommen_kwh: float
    abgabe_ac_w: float
    notstrom_dc_w: float
    netzspannung_v: float
    frequenz_hz: float
    batteriespannung_v: float

@dataclass(frozen=True)
class BatterieManager:
    entnommen_kwh: float
    ladungswert_ah: float
    abgabe_dc_w: float
    pa_w: float
    strom_a: float
    spannung_v: float
    temperatur_c: float


def parse_tm_b(text):
    return Temperatur(feld(text, 'IT', 'C'), feld(text, 'AT', 'C'))


def parse_in_w(text):
    """IN_W tolerant gegen Komma/Punkt und variable Abstaende/Zeilenumbruch.

    Nur echte vollstaendige Antworten werden als gueltig uebernommen.
    Pp ist externe DC-Notstromeinspeisung, keine PV-Leistung.
    """
    sd = feld(text, 'SD', 'kWh')
    pw = feld(text, 'Pw', 'W')
    pp = feld(text, 'Pp', 'W')
    # Spaetere drei unbeschriftete Werte hinter Pp: VAC, Hz, VBat.
    tail = re.search(r'(?<!\w)Pp\s*:?\s*' + NUM +
                     r'\s*W\s+' + NUM + r'\s*V\s+' + NUM +
                     r'\s*Hz\s+' + NUM + r'\s*V(?!\w)', text, re.I)
    if not tail:
        raise ValueError('IN_W: drei Messwerte nach Pp fehlen oder haben anderes Format: %r' % text[:160])
    ac, hz, batt = (wert(x) for x in tail.groups()[1:])
    import math
    if not all(math.isfinite(x) for x in (sd, pw, pp, ac, hz, batt)):
        raise ValueError('IN_W: ungueltige Zahl')
    if not (0 <= sd <= 20.0 and 0 <= ac <= 300 and 0 <= hz <= 70 and 0 <= batt <= 100):
        raise ValueError('IN_W: Messwerte ausserhalb Plausibilitaetsbereich')
    return Inselwechselrichter(sd, pw, pp, ac, hz, batt)


def parse_bm_w(text):
    return BatterieManager(
        feld(text, 'En', 'kWh'), feld(text, 'Ch', 'Ah'),
        feld(text, 'Pd', 'W'), feld(text, 'Pa', 'W'),
        feld(text, 'Ia', 'A'), feld(text, 'U', 'V'), feld(text, 'T', 'C'))


@dataclass(frozen=True)
class Motor:
    minuten_seit_start: int
    drehzahl_rpm: int
    kruemmer_c: float
    abgas_c: float
    leistung_w: float
    spruehkuehlung_prozent: float
    betriebsstunden_h: float
    wirkungsgrad_prozent: float
    zustand: str
    zustandskennung: Optional[str] = None


def parse_mo_w(text):
    """Altes und neues MO_W-Format (optional mit AAN/AMAUS/AAUS)."""
    pattern = (r'(?<!\w)(\d+)\s*min\s+(\d+)\s*rpm\s+'
               r'Tk\s*' + NUM + r'\s+Tp\s*' + NUM + r'\s+'
               + NUM + r'\s*W\s+' + NUM + r'\s*%\s+'
               r'h\s*:\s*' + NUM + r'\s+' + NUM + r'\s*%')
    m = re.search(pattern, text, re.I)
    if not m:
        raise ValueError('MO_W: erwartete Werte fehlen')
    values = [wert(x) for x in m.groups()]
    if not all(__import__('math').isfinite(v) for v in values):
        raise ValueError('MO_W: nicht endliche Werte')
    minuten, rpm, tk, tp, watt, spray, hours, eta = values
    if min(values[0], rpm, watt, spray, hours, eta) < 0 or spray > 100 or eta > 100:
        raise ValueError('MO_W: unplausible Werte')
    # Aktiv nur bei deutlich positiver Leistung UND Drehzahl. Der Status
    # INAKTIV darf hieraus nicht sicher abgeleitet werden.
    # Zustandswort wird nur am Anfang des extrahierten Motorsegments akzeptiert.
    prefix = text[:m.start()].strip()
    kennung = prefix if prefix in ('AAN', 'AMAUS', 'AAUS') else None
    if kennung == 'AAN':
        state = 'LAEUFT' if rpm > 0 else 'WIDERSPRUCH'
    elif kennung == 'AMAUS':
        state = 'REINIGUNG' if rpm == 0 else 'WIDERSPRUCH'
    elif kennung == 'AAUS':
        state = 'AUS_BEREIT' if rpm == 0 else 'WIDERSPRUCH'
    else:
        state = 'LAEUFT' if rpm > 0 else 'AUS_STATUS_UNBEKANNT'
    return Motor(int(minuten), int(rpm), tk, tp, watt, spray, hours, eta, state, kennung)


def soc_aus_entnahme(entnommen_kwh, kapazitaet_kwh):
    """SoC in %, None bei ungueltigem Zählerstand statt stiller Begrenzung."""
    import math
    if not (math.isfinite(entnommen_kwh) and math.isfinite(kapazitaet_kwh)):
        return None
    if kapazitaet_kwh <= 0 or entnommen_kwh < 0 or entnommen_kwh > kapazitaet_kwh:
        return None
    from decimal import Decimal, ROUND_HALF_UP
    return float((Decimal('100') * (Decimal(str(kapazitaet_kwh)) - Decimal(str(entnommen_kwh)))
                  / Decimal(str(kapazitaet_kwh))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
