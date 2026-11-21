"""Diagnose: 24-h-Hausmodell mit 30-min-Fensterwärme aus forecast.solar.

Keine Steuerbefehle. Konstante Aussentemperatur; kein gemessener WP-Istzustand.
"""
from datetime import datetime, timedelta
from parameter import Parameter, STARTPLAENE
from energie_thermisch import temperatur_nach_schritt


def prognose_thermik(innen_c, aussen_c, p=None, zeit=None, fenster_halbstunden=None):
    p = p or Parameter()
    zeit = zeit or datetime.now().astimezone()
    solar = {}
    tage_ohne_verlauf = []
    if fenster_halbstunden is None:
        fenster_halbstunden = {}
    for date, liste in fenster_halbstunden.items():
        if liste is None:
            tage_ohne_verlauf.append(date)
            continue
        for teil in liste:
            solar[teil['von'][:16]] = float(teil['kw_th'])
    # Gleitender 24h-Horizont: fuer jeden Zeitschritt die vorhergesagte
    # Halbstunden-Waermeleistung waehlen, ohne neue forecast.solar Anfrage.
    beginn = zeit.replace(minute=(zeit.minute//30)*30, second=0, microsecond=0)
    schritte = []
    for i in range(48):
        t = beginn + timedelta(minutes=30*i)
        k = t.isoformat()[:16]
        schritte.append((t, solar.get(k), k[:10]))
    fehlende = sorted(set(tag for _, kw, tag in schritte if kw is None))
    ergebnisse = []
    for anzahl, starts in STARTPLAENE.items():
        innen = innen_c
        minimum = maximum = innen
        solar_e = 0.0
        trace = []
        for t, solar_kw, tag in schritte:
            # Nur zukuenftige Starts modellieren, keine unbekannten realen
            # vergangenen Starts unterstellen. Alte Tagesplaene sind keine Ist-Daten.
            # Bei bereits laufendem Motor liefert dies KEINE belastbare Prognose.
            moment = t.hour + t.minute/60.
            on = any(0 <= (moment - start) < p.bhkw_laufzeit_h for start in starts)
            # 23:20-Start reicht in den Folgetag. Diesen Teil aber nicht
            # ohne Istzustandsdaten als sicher laufend unterstellen.
            kw = solar_kw if solar_kw is not None else 0.
            solar_e += kw*0.5
            innen = temperatur_nach_schritt(innen, aussen_c, on, 'STOP', 0.5, p, solar_kw=kw)
            minimum = min(minimum, innen)
            maximum = max(maximum, innen)
            trace.append({'zeit': (t+timedelta(minutes=30)).isoformat(),
                          'innen_c': round(innen, 3),
                          'sonne_kw': solar_kw, 'bhkw_modell_an': on})
        ergebnisse.append({'starts': anzahl,
                          'startzeiten': [f'{int(s):02d}:{int(round((s % 1)*60)):02d}' for s in starts],
                          'innen_nach_24h_c': round(innen, 2),
                          'minimum_c': round(minimum, 2),
                          'maximum_c': round(maximum, 2),
                          'fensterwaerme_im_horizont_kwh': round(solar_e, 3),
                          'temperaturverlauf': trace})
    return {'zeitpunkt': zeit.isoformat(), 'aussen_annahme_c': aussen_c,
            'innen_start_c': innen_c, 'schritt_min': 30,
            'heizlast_aktuell_kw': round(p.waermeverlust_kw_pro_k*(innen_c-aussen_c), 3),
            'modell': 'konstante Aussentemperatur, ohne WP/interne Gewinne; Fensterwaerme aus forecast.solar',
            'sonne_integriert': bool(solar),
            'fehlende_solar_tage': fehlende,
            'hinweis_fehlende_daten': 'Fehlende Solarintervalle werden mit 0 gerechnet, aber als fehlend ausgewiesen; Prognose nur eingeschraenkt aussagekraeftig.' if fehlende else None,
            'startstatus': 'NICHT BERUECKSICHTIGT: aktueller BHKW-Zustand und Vortagslaeufe',
            'nur_diagnose': True, 'szenarien': ergebnisse}
