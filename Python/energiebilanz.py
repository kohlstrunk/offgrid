"""Passive Leistungs-/Energieauswertung mit durch Voll-Ladungsreset bekannter SoC-Berechnung.

Pw: abgegebene AC-Leistung des Insel-WR, Pd: abgegebene DC-Leistung des BM.
SD/En: kumulierte Entnahme seit Voll-Ladung; SoC ergibt sich aus Nennkapazität minus Entnahme. Negative
Zählerdifferenzen (Reset/Überlauf) dürfen nicht als Ladung interpretiert werden.
"""
from datetime import datetime, timezone
import math
from sensoren import soc_aus_entnahme


class Energiebilanz:
    def __init__(self):
        self.letzte = {}
        self.zaehler_differenz_kwh = {'IN_W': 0.0, 'BM_W': 0.0}
        self.zaehler_resets = {'IN_W': 0, 'BM_W': 0}
        self.leistungsintegral_kwh = {'IN_W': 0.0, 'BM_W': 0.0}
        self.intervalle = {'IN_W': 0, 'BM_W': 0}
        self.letzte_leistung_w = {'IN_W': None, 'BM_W': None}
        self.letzte_zaehler_kwh = {'IN_W': None, 'BM_W': None}
        self.max_intervall_s = 1800  # verhindert Integration über Kommunikationslücken

    def aufnehmen(self, name, wert, zeit=None):
        if name not in self.zaehler_differenz_kwh:
            return
        zeit = zeit or datetime.now(timezone.utc)
        if name == 'IN_W':
            zaehler, leistung = wert.entnommen_kwh, wert.abgabe_ac_w
        else:
            zaehler, leistung = wert.entnommen_kwh, wert.abgabe_dc_w
        if not all(math.isfinite(x) and x >= 0 for x in (zaehler, leistung)):
            return
        vorher = self.letzte.get(name)
        if vorher:
            alter = (zeit - vorher[0]).total_seconds()
            if zaehler < vorher[1] - 1e-6:
                self.zaehler_resets[name] += 1
            elif 0 < alter <= self.max_intervall_s:
                self.zaehler_differenz_kwh[name] += max(0.0, zaehler - vorher[1])
            if 0 < alter <= self.max_intervall_s:
                self.leistungsintegral_kwh[name] += (vorher[2] + leistung) / 2000 * alter / 3600
                self.intervalle[name] += 1
        self.letzte[name] = (zeit, zaehler, leistung)
        self.letzte_leistung_w[name] = leistung
        self.letzte_zaehler_kwh[name] = zaehler

    def bericht(self):
        return {
            'beschreibung': 'Beobachtete Abgabe und Zaehlerzunahme seit Dienststart; SoC aus Entnahme seit Voll-Ladung',
            'IN_W': self._eintrag('IN_W', 'AC'),
            'BM_W': self._eintrag('BM_W', 'DC'),
            'hinweis': 'AC und DC nicht direkt addieren; SoC aus Entnahmezaehlern ist vom korrekten Voll-Ladungs-Reset abhaengig',
        }

    def _eintrag(self, name, art):
        return {'seite': art, 'soc_prozent': (soc_aus_entnahme(self.letzte_zaehler_kwh[name], 20.0 if name == 'IN_W' else 7.0) if self.letzte_zaehler_kwh[name] is not None else None),
                'restenergie_kwh': (round((20.0 if name == 'IN_W' else 7.0) - self.letzte_zaehler_kwh[name], 3) if self.letzte_zaehler_kwh[name] is not None and soc_aus_entnahme(self.letzte_zaehler_kwh[name], 20.0 if name == 'IN_W' else 7.0) is not None else None),
                'abgabeleistung_w': self.letzte_leistung_w[name],
                'entnahmezaehler_kwh': self.letzte_zaehler_kwh[name],
                'zaehlerzuwachs_seit_start_kwh': round(self.zaehler_differenz_kwh[name], 4),
                'abgabe_aus_leistungsintegral_kwh': round(self.leistungsintegral_kwh[name], 4),
                'integrierte_intervalle': self.intervalle[name],
                'zaehler_resets': self.zaehler_resets[name]}
