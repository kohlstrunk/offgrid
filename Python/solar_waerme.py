"""Tagesprognose des solaren Fenster-Waermeeintrags.

Fiktives senkrechtes Sued-PV-Feld: 2,2 kWp bei 90 Grad.
Die Ertragszahl ist NUR ein Proxy fuer 11 m² Suedfenster und wird
mit dem vom Anlagenbetreiber vorgegebenen Faktor 3 in kWh_th umgesetzt.
Keine elektrische Erzeugung; Zeitreihe separat aus forecast.solar.
"""
from datetime import timedelta

FENSTER_FELD = 'fenster_sued_2_2kwp_90grad'
FENSTER_FAKTOR = 3.0


def fenster_waerme_tage(prognosen, heute):
    """Gibt heutige/morgige PV-Proxy- und Waermeenergien zurueck.

    Fehlende Werte bleiben None statt 0; somit bleiben API-Fehler sichtbar.
    """
    daten = prognosen.get(FENSTER_FELD, {})
    morgen = (heute + timedelta(days=1)).isoformat()
    heute = heute.isoformat()
    def wert(tag):
        raw = daten.get(tag)
        return None if raw is None else round(float(raw) * FENSTER_FAKTOR, 3)
    return {'heute_kwh_th': wert(heute), 'morgen_kwh_th': wert(morgen),
            'pv_proxy_heute_kwh': daten.get(heute),
            'pv_proxy_morgen_kwh': daten.get(morgen),
            'faktor': FENSTER_FAKTOR, 'flaeche_m2': 11,
            'anlage': {'kwp': 2.2, 'neigung_grad': 90, 'azimut_grad': 0},
            'typ': 'Fiktive PV-Anlage als solare Fenster-Waermeprognose; kein Stromertrag',
            'hinweis': 'Tagesenergie; Zeitreihe separat unter fenster_halbstunden.'}


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo('Europe/Berlin')


def fenster_waerme_halbstunden(kumuliert_wh, tage_kwh, heute):
    """30-min-Fensterwaerme aus kumulierten forecast.solar-Wh je Tag.

    Die Differenzen werden durch lineare Interpolation der kumulierten Wh
    bestimmt (also auch bei stuendlichen Originalwerten). Fehlende Zeitreihen
    werden NICHT mit kuenstlichen Sonnenstunden aufgefuellt.

    Ausgabe ist eine Liste lokaler Zeitintervalle mit kWh_th und mittleren kW_th.
    """
    ausgabe = {}
    for tag in (heute, heute + timedelta(days=1)):
        key = tag.isoformat()
        punkte = []
        for stamp, val in kumuliert_wh.items():
            try:
                t = datetime.fromisoformat(stamp)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=BERLIN)
                else:
                    t = t.astimezone(BERLIN)
                if t.date() == tag:
                    punkte.append((t, float(val)))
            except (TypeError, ValueError):
                continue
        punkte.sort(key=lambda x: x[0])
        if not punkte or key not in tage_kwh:
            ausgabe[key] = None
            continue
        # Mit 0 Wh zu Tagesbeginn und Tagessumme um 24 Uhr abschliessen.
        # Zwischen gemeldeten Messpunkten lineare kumulative Energie.
        start = datetime(tag.year, tag.month, tag.day, tzinfo=BERLIN)
        ende_tag = datetime.combine(tag + timedelta(days=1), datetime.min.time(), BERLIN)
        gesamt_wh = float(tage_kwh[key]) * 1000
        daten = [(start, 0.)] + [(t, v) for t, v in punkte if start < t < ende_tag]
        daten.append((ende_tag, gesamt_wh))
        # Falls das API inkonsistente kumulative Daten liefert, keine negative Energie zulassen.
        if any(daten[i + 1][1] < daten[i][1] - 0.01 for i in range(len(daten) - 1)):
            ausgabe[key] = None
            continue
        def wert(t):
            if t <= daten[0][0]:
                return daten[0][1]
            if t >= daten[-1][0]:
                return daten[-1][1]
            for j in range(1, len(daten)):
                t1, w1 = daten[j-1]
                t2, w2 = daten[j]
                if t <= t2:
                    total = (t2-t1).total_seconds()
                    return w1 + (w2-w1) * (t-t1).total_seconds()/total if total else w2
            return daten[-1][1]
        schritte = []
        for n in range(48):
            a = start + timedelta(minutes=30*n)
            b = start + timedelta(minutes=30*(n+1))
            e = max(0., (wert(b)-wert(a)) * FENSTER_FAKTOR / 1000)
            schritte.append({'von': a.isoformat(), 'bis': b.isoformat(),
                             'kwh_th': round(e, 5), 'kw_th': round(e*2, 5)})
        ausgabe[key] = schritte
    return ausgabe
