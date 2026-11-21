"""Passives Mithören von Geräteantworten; gültige Werte erneuern den Zeitstempel."""
from dataclasses import asdict
from datetime import datetime, timezone
import time
import re
from sensoren import parse_tm_b, parse_in_w, parse_bm_w, parse_mo_w, soc_aus_entnahme

PARSER = {'TM_B': parse_tm_b, 'IN_W': parse_in_w, 'BM_W': parse_bm_w, 'MO_W': parse_mo_w}
SIGNATURE = {'TM_B': 'IT ', 'IN_W': 'SD ', 'BM_W': 'Ch:', 'MO_W': 'rpm Tk '}


class SensorCache:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._werte = {}
        self._letzter_motorlauf = float('-inf')
        self.diagnose = {name: {'gueltige_antworten': 0, 'parserfehler': 0, 'letzter_fehler': None} for name in PARSER}

    def aufnehmen(self, zeile):
        """Erkennt Zeile auch bei vorangestelltem Terminaltext; keine Befehlszuordnung nötig."""
        for name, marker in SIGNATURE.items():
            match = re.search(re.escape(marker), zeile, re.IGNORECASE)
            if not match:
                continue
            text = zeile.strip() if name == 'MO_W' else zeile[match.start():].strip()
            try:
                daten = PARSER[name](text)
            except ValueError as exc:
                self.diagnose[name]['parserfehler'] += 1
                self.diagnose[name]['letzter_fehler'] = str(exc)
                continue
            self.diagnose[name]['gueltige_antworten'] += 1
            zeitpunkt = self.clock()
            if name == 'MO_W' and daten.drehzahl_rpm > 0:
                self._letzter_motorlauf = zeitpunkt
            self._werte[name] = (daten, zeitpunkt, datetime.now(timezone.utc).isoformat())
            return name
        return None

    def alter(self, name):
        eintrag = self._werte.get(name)
        return None if eintrag is None else max(0, self.clock() - eintrag[1])

    def veraltet(self, name, sekunden=600):
        alter = self.alter(name)
        return alter is None or alter >= sekunden

    def motorstatus(self, maxalter=600):
        """Nur aktuelle MO_W-Meldung, keine Schaltfreigabe.

        Nach fehlendem Praefix gilt ein stehender Motor als nicht startbereit.
        """
        result = {'zustand': 'UNBEKANNT', 'startbereit': False,
                  'grund': 'BHKW-Zustand nicht ausreichend bekannt'}
        entry = self._werte.get('MO_W')
        if not entry or self.veraltet('MO_W', maxalter):
            return result
        motor = entry[0]
        if motor.zustand == 'WIDERSPRUCH':
            return dict(result, zustand='WIDERSPRUCH',
                        grund='Kennung und Drehzahl widersprechen sich')
        if motor.drehzahl_rpm > 0:
            return dict(result, zustand='LAEUFT', grund='Drehzahl > 0')
        if motor.zustandskennung == 'AMAUS':
            return dict(result, zustand='REINIGUNG', grund='AMAUS: Reinigung nicht abgeschlossen')
        if motor.zustandskennung == 'AAUS':
            if motor.kruemmer_c < 70:
                return {'zustand': 'AUS_BEREIT', 'startbereit': True,
                        'grund': 'AAUS und Tk < 70 C; nur technische Teilbedingung'}
            return dict(result, zustand='AUS_ZU_HEISS', grund='AAUS, aber Tk >= 70 C')
        return dict(result, zustand='AUS_STATUS_UNBEKANNT',
                    grund='Drehzahl 0; AAUS/AMAUS in MO_W nicht bekannt')

    def gesamt_soc(self, maxalter=600):
        """Kapazitaetsgewichteter SoC, nur wenn BEIDE Werte aktuell sind."""
        ergebnis = {'gueltig': False, 'soc_prozent': None,
                    'restenergie_kwh': None, 'nennkapazitaet_kwh': 27.0,
                    'hinweis': 'Gemeinsame Reserve nur fuer Tagesplanung; Leistungsgrenzen bleiben getrennt'}
        if any(self.veraltet(name, maxalter) for name in ('IN_W', 'BM_W')):
            ergebnis['grund'] = 'Mindestens ein Batteriewert fehlt oder ist veraltet'
            return ergebnis
        sd = self._werte['IN_W'][0].entnommen_kwh
        en = self._werte['BM_W'][0].entnommen_kwh
        if soc_aus_entnahme(sd, 20.0) is None or soc_aus_entnahme(en, 7.0) is None:
            ergebnis['grund'] = 'Unplausibler Entnahmezaehler'
            return ergebnis
        rest = (20.0 - sd) + (7.0 - en)
        ergebnis.update(gueltig=True, restenergie_kwh=round(rest, 3),
                        soc_prozent=round(rest / 27.0 * 100, 2), grund='OK')
        return ergebnis

    def bericht(self, maxalter=600):
        return {
            name: {
                'empfangen_utc': self._werte[name][2] if name in self._werte else None,
                'alter_sekunden': round(self.alter(name), 1) if name in self._werte else None,
                'gueltig': not self.veraltet(name, maxalter),
                'werte': asdict(self._werte[name][0]) if name in self._werte else None,
                'diagnose': self.diagnose[name].copy(),
                'soc_prozent': (soc_aus_entnahme(self._werte[name][0].entnommen_kwh,
                                 20.0 if name == 'IN_W' else 7.0)
                                 if name in ('IN_W', 'BM_W') and name in self._werte else None),
            }
            for name in PARSER
        }
