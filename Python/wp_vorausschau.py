"""PV-prognosebasierte WP-Einschaltplanung, ohne Funk oder HTTP-Zugriffe.

Die bestehende Fuenf-Minuten-PV-Vorhersage, Zwei-Akku-Bilanz und Hauswaerme-
rechnung werden verwendet. Die Regelung ist ein vereinfachtes Modell; nur
bereits gespeicherte forecast.solar-Daten werden gelesen.
"""
from datetime import datetime, timedelta
from math import isfinite

from energie_elektrisch import Speicher, schritt_mit_bhkw_abschaltung
from energie_thermisch import temperatur_nach_schritt
from planung_automatik import pv_energy, fenster_energy, REAL, TZ


def _intervalle(jetzt, pv_state, p):
    """PV-Tag bis spaetestens 22 Uhr; echte Zeitreihen bestimmen Tagesende."""
    bis = jetzt.replace(hour=22, minute=0, second=0, microsecond=0)
    if jetzt >= bis:
        return None, 'PV_TAG_BEENDET'
    at = jetzt
    daten = []
    fallback = set()
    while at < bis:
        ende = min(at + timedelta(minutes=5), bis)
        h = (ende - at).total_seconds() / 3600
        pv1 = pv_energy(REAL[0], at, h, pv_state, fallback)
        pv2 = pv_energy(REAL[1], at, h, pv_state, fallback)
        solar = fenster_energy(at, ende, pv_state, fallback)
        if pv1 is None or pv2 is None or solar is None:
            return None, 'PV_PROGNOSE_FEHLT'
        if any(not isfinite(float(x)) or float(x) < 0 for x in (pv1, pv2, solar)):
            return None, 'PV_PROGNOSE_UNGUELTIG'
        daten.append((at, h, pv1 / h, pv2 / h, solar / h))
        at = ende
    # Nach dem letzten prognostizierten PV-Ertrag besteht kein Grund, die WP
    # weiterlaufen zu lassen; ein Sommer-Sonnenuntergang kann nach 18 Uhr liegen.
    letzte_pv = max((i for i, v in enumerate(daten) if v[2] + v[3] > 0.02), default=None)
    if letzte_pv is None:
        return None, 'KEIN_PV_ERTRAG_MEHR'
    daten = daten[:letzte_pv + 1]
    return daten, ('ERSATZPROFIL:' + ','.join(sorted(fallback)) if fallback else 'ZEITPROFIL')


def _lauf(profil, p, innen, aussen, e1, e2, wp_ab_index=None, soc_aus=90.0, wp_bis_index=None, trace=False):
    bat = Speicher(max(0, min(p.batterie1_kwh, e1)), max(0, min(p.batterie2_kwh, e2)))
    t = innen
    ueberschuss = 0.0
    wp_kwh = 0.0
    erster_wp_soc = None
    wp_start = None
    letzte_wp_zeit = None
    abregelung_schritte = []
    kap = p.batterie1_kwh + p.batterie2_kwh
    unten = kap * soc_aus / 100
    for i, (at, h, pv1, pv2, solar_kw) in enumerate(profil):
        soc = 100 * (bat.eins_kwh + bat.zwei_kwh) / kap
        wp = bool(wp_ab_index is not None and i >= wp_ab_index
                  and (wp_bis_index is None or i < wp_bis_index))
        # Direkte thermische Grenze mit Vorhersage eines Zeitschritts.
        if wp and (soc <= soc_aus or t >= p.maximum_c):
            wp = False
        if wp:
            t_test = temperatur_nach_schritt(t, aussen, False, 'HEIZEN', h, p, solar_kw=solar_kw)
            if t_test > p.maximum_c + 1e-8:
                wp = False
        # Keine Energie fuer WP entnehmen, wenn die erwartete Bilanz unter
        # die SOC-Abschaltschwelle fallen wuerde (5-min-Näherung).
        if wp:
            probe = Speicher(bat.eins_kwh, bat.zwei_kwh)
            schritt_mit_bhkw_abschaltung(probe, pv1, pv2, 0.0,
                                         p.basislast_kw + p.wp_elektrisch_kw, h, p)
            if probe.eins_kwh + probe.zwei_kwh < unten - 1e-8:
                wp = False
        if wp:
            if wp_start is None:
                wp_start = at
                erster_wp_soc = round(soc, 2)
            wp_kwh += p.wp_elektrisch_kw * h
            letzte_wp_zeit = at + timedelta(hours=h)
        _, spill, _, _, _ = schritt_mit_bhkw_abschaltung(
            bat, pv1, pv2, 0.0, p.basislast_kw + (p.wp_elektrisch_kw if wp else 0), h, p)
        ueberschuss += spill
        if spill > 0.001:
            abregelung_schritte.append(i)
        t = temperatur_nach_schritt(t, aussen, False, 'HEIZEN' if wp else 'STOP',
                                   h, p, solar_kw=solar_kw)
    result = {'abregelung_kwh': round(ueberschuss, 4),
            'soc_ende_prozent': round(100 * (bat.eins_kwh + bat.zwei_kwh) / kap, 3),
            'wp_strom_kwh': round(wp_kwh, 4), 'temperatur_ende_c': round(t, 3),
            'wp_start': wp_start, 'wp_ende': letzte_wp_zeit,
            'soc_wp_start_prozent': erster_wp_soc}
    if trace:
        result['abregelung_schritte'] = abregelung_schritte
    return result


def wp_start_prognose(jetzt, pv_state, p, innen, aussen, e1, e2):
    """Findet spaetesten sinnvollen WP-Start bei drohender PV-Abregelung.

    Ziel: prognostizierte Abregelung minimieren und dabei Batterien am Ende
    des PV-Tages wieder voll haben (wenn dies laut Basismodell erreichbar ist).
    Die WP kann nur 1 kW aufnehmen; bei temperaturbedingter Begrenzung oder
    Fehlprognosen bleibt PV-Abregelung moeglich.
    """
    jetzt = jetzt.astimezone(TZ)
    profil, quelle = _intervalle(jetzt, pv_state, p)
    if not profil:
        return {'starten': False, 'grund': quelle}
    baseline = _lauf(profil, p, innen, aussen, e1, e2,
                     soc_aus=p.wp_soc_aus_prozent, trace=True)
    out = {'starten': False, 'grund': 'KEINE_DROHENDE_ABREGELUNG',
           'pv_profil': quelle, 'pv_abregelung_ohne_wp_kwh': baseline['abregelung_kwh'],
           'soc_ende_ohne_wp_prozent': baseline['soc_ende_prozent'],
           'ziel_soc_prozent': 100.0,
           'wp_leistung_kw': p.wp_elektrisch_kw,
           'einschalt_soc_berechnet_prozent': None,
           'einschaltzeit': None}
    if baseline['abregelung_kwh'] <= 0.05:
        return out
    # Kandidaten in aufsteigender zeitlicher Reihenfolge; bei fast gleicher
    # Abregelung den spaetesten und damit energiesparsamsten Start waehlen.
    # Zwei Freiheitsgrade: rechtzeitig EIN und noch waehrend PV-Erzeugung
    # wieder AUS. Durchlauf bis 18:00 wuerde den SOC gegen Abend entladen.
    # Zehn-/Fuenfzehn-Minuten-Raster zur Begrenzung der Rechenzeit.
    kandidaten = []
    stop_indices = sorted(set(list(range(3, len(profil) + 1, 3)) +
                              [len(profil), max(baseline['abregelung_schritte']) + 1]))
    start_indices = sorted(set([0] + list(range(3, len(profil), 3))))
    for i in start_indices:
        for j in stop_indices:
            if j <= i:
                continue
            candidate = _lauf(profil, p, innen, aussen, e1, e2, i,
                              soc_aus=p.wp_soc_aus_prozent, wp_bis_index=j)
            if candidate['wp_strom_kwh'] < 0.1:
                continue
            reduktion = baseline['abregelung_kwh'] - candidate['abregelung_kwh']
            if reduktion < 0.1:
                continue
            # Die Batterie muss am PV-Tagesende wieder nahezu so voll sein
            # wie ohne WP; 100% nur wenn PV-Energie dies auch ermoeglicht.
            soc_min = min(99.5, baseline['soc_ende_prozent'] - 0.5)
            if candidate['soc_ende_prozent'] < soc_min:
                continue
            kandidaten.append((i, j, candidate))
    if not kandidaten:
        out['grund'] = 'WP_KANN_ABREGELUNG_NICHT_SINNVOLL_VERMEIDEN'
        return out
    best_spill = min(v['abregelung_kwh'] for _, _, v in kandidaten)
    # Bis 0,05 kWh Mehr-Abregelung tolerieren, um spaeter anzuschalten.
    usable = [(i, j, v) for i, j, v in kandidaten if v['abregelung_kwh'] <= best_spill + 0.05]
    i, j, optimum = max(usable, key=lambda x: x[0])
    out.update({'starten': i == 0, 'grund': 'WP_JETZT_STARTEN' if i == 0 else 'WP_START_SPÄTER',
                'einschaltzeit': profil[i][0].isoformat(timespec='minutes'),
                'ausschaltzeit_prognose': (profil[j][0] if j < len(profil) else
                                            profil[-1][0] + timedelta(hours=profil[-1][1])).isoformat(timespec='minutes'),
                'einschalt_soc_berechnet_prozent': optimum['soc_wp_start_prozent'],
                'pv_abregelung_mit_wp_kwh': optimum['abregelung_kwh'],
                'soc_ende_mit_wp_prozent': optimum['soc_ende_prozent'],
                'wp_strom_geplant_kwh': optimum['wp_strom_kwh'],
                'temp_ende_mit_wp_c': optimum['temperatur_ende_c']})
    return out
