"""Rollierende 24-h-Planentscheidung (NUR SIMULATION, KEINE AKTORBEFEHLE).

Vereinfachungen: konstante Aussentemperatur, Hausgrundlast 250 W,
PV-Verlauf aus gecachten kumulierten Wh (sonst klar gekennzeichnetes Ersatzprofil),
spannungsgeregelte 800-W-Einspeisung nur approximiert, keine gemessene WP.
"""
from datetime import datetime, timedelta
from math import sin, pi, isfinite
from zoneinfo import ZoneInfo

from parameter import Parameter, STARTPLAENE
from energie_elektrisch import Speicher, schritt_mit_bhkw_abschaltung as batterie_schritt
from energie_thermisch import temperatur_nach_schritt
from solar_waerme import FENSTER_FELD, FENSTER_FAKTOR
from energie_boost import boost_kwh_im_intervall

TZ = ZoneInfo('Europe/Berlin')
REAL = ('pv1_4kwp_38grad', 'pv2_2_5kwp_20grad')


def _value_at(points, at):
    if not points:
        return 0.0
    if at <= points[0][0]:
        return 0.0
    for i in range(1, len(points)):
        if at <= points[i][0]:
            a, ea = points[i - 1]
            b, eb = points[i]
            return ea + (eb - ea) * (at - a).total_seconds() / (b - a).total_seconds()
    return points[-1][1]


def _interval_energy(cumulative, day_energy, start, end):
    """Zeitlich verteilte Energie innerhalb eines Kalendertages in kWh."""
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    finish = midnight + timedelta(days=1)
    points = [(midnight, 0.0)]
    for stamp, raw in cumulative.items():
        try:
            dt = datetime.fromisoformat(stamp)
            dt = dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)
            val = float(raw) / 1000.0
            if midnight < dt < finish and isfinite(val):
                points.append((dt, val))
        except (ValueError, TypeError):
            pass
    points.append((finish, day_energy))
    points.sort(key=lambda x: x[0])
    if len(points) < 3 or any(points[i][1] > points[i + 1][1] + 1e-6 for i in range(len(points) - 1)):
        return None
    return max(0.0, _value_at(points, end) - _value_at(points, start))


def pv_energy(field, start, duration_h, state, fallback_days):
    """PV in kWh, aus Cache-Zeitreihe oder Tagesmenge mit Sinusprofil."""
    day = start.date().isoformat()
    amount = (state.get('prognosen_kwh', {}).get(field) or {}).get(day)
    if amount is None:
        return None
    amount = float(amount)
    if not isfinite(amount) or amount < 0:
        return None
    end = start + timedelta(hours=duration_h)
    series = (state.get('zeitverlauf_wh', {}).get(field) or {})
    e = _interval_energy(series, amount, start, end) if series else None
    if e is not None:
        return e
    fallback_days.add(field + '/' + day)
    # Sinus-normalisiertes Ersatzprofil von 06:00 bis 18:00 Uhr.
    def cumulative(h):
        h = max(6.0, min(18.0, h))
        return amount * (1 - __import__('math').cos(pi * (h - 6.0) / 12.0)) / 2
    h0 = start.hour + start.minute / 60 + start.second / 3600
    return max(0.0, cumulative(h0 + duration_h) - cumulative(h0))


def _solar_energy(start, end, fenster):
    intervals = fenster.get(start.date().isoformat())
    if intervals is None:
        return None
    for entry in intervals:
        try:
            a = datetime.fromisoformat(entry['von'])
            b = datetime.fromisoformat(entry['bis'])
            if a <= start and end <= b:
                return float(entry['kw_th']) * (end - start).total_seconds() / 3600.0
        except (KeyError, ValueError, TypeError):
            pass
    return None


def fenster_energy(start, end, pv_state, fallback_days):
    """Fenster-Waerme (kWh): echte 30-Min-Werte, sonst Prognose-Zeitreihe,
    sonst ein aus der Tagesenergie berechnetes Ersatzprofil.

    Fehlende Tagesenergiemengen duerfen NICHT als Null ersetzt werden.
    """
    total = 0.0
    at = start
    while at < end:
        midnight = datetime(at.year, at.month, at.day, tzinfo=TZ) + timedelta(days=1)
        finish = min(end, midnight)
        day = at.date().isoformat()
        actual = _solar_energy(at, finish, pv_state.get('fenster_halbstunden', {}))
        if actual is not None:
            total += actual
        else:
            # pv_energy bevorzugt vorhandene echte Zeitreihen und markiert nur
            # bei fehlender/ungueltiger Reihe das genutzte Ersatzprofil.
            proxy = pv_energy(FENSTER_FELD, at,
                              (finish-at).total_seconds()/3600.0,
                              pv_state, fallback_days)
            if proxy is None:
                return None
            total += proxy * FENSTER_FAKTOR
        at = finish
    return total


def _starts_for(plan, now):
    entries = []
    # Beide Tage im 24-h-Horizont plus Randtage fuer Zeitueberhaenge.
    for shift in (-1, 0, 1, 2):
        day = now.date() + timedelta(days=shift)
        for slot in STARTPLAENE[plan]:
            entries.append(datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(hours=slot))
    return sorted(entries)


def simuliere_plan(plan, now, ti, ta, e1, e2, motor, pv_state, p, reserve_kwh=None, boost=None):
    reserve_kwh = p.plan_gesamtreserve_kwh if reserve_kwh is None else reserve_kwh
    dt_min = 5
    horizon = 24 * 60 // dt_min
    ends = now + timedelta(hours=24)
    runs = []
    starts = _starts_for(plan, now)
    # Vergangene schematische Starts werden NICHT als ausgefuehrt angenommen.
    starts = [t for t in starts if now <= t < ends]
    if motor.drehzahl_rpm > 0:
        remaining = max(0, 240 - motor.minuten_seit_start)
        if remaining:
            runs.append((now, now + timedelta(minutes=remaining)))
        # Ein erneuter Start vor Stop + 20min wird nur virtuell entfernt.
        earliest = now + timedelta(minutes=remaining + 20)
        starts = [t for t in starts if t >= earliest]
    runs += [(t, t + timedelta(hours=4)) for t in starts]
    runs.sort(key=lambda r: r[0])
    # Keine ueberlappenden oder unzulaessig nahe aneinander liegenden Starts.
    for a, b in zip(runs, runs[1:]):
        if b[0] < a[1] + timedelta(minutes=20):
            return {'anzahl': plan, 'zulaessig': False, 'grund': 'BHKW-Startabstand verletzt'}
    t = ti
    bat = Speicher(e1, e2)
    min_t = max_t = t
    min_energy = e1 + e2
    min_b1 = e1
    mangel = ueberschuss = wp_kwh = pv_kwh = sonne_kwh = 0.0
    fallback = set()
    fehlende = set()
    sample = []
    abgeschaltete_laeufe = set()
    abschaltungen = []
    # Sobald der Plan BHKW-Betrieb vorsieht, darf keine elektrische
    # Kuehlung als Gegenmassnahme fuer die zwangsweise BHKW-Waerme erfolgen.
    kuehlen_erlaubt = not bool(runs)
    # Fuenf-Minuten-Raster nur fuer die allgemeine Simulation. BHKW-Schaltzeiten
    # sind harte Ereignisgrenzen: ein Start um 14:00 darf niemals schon im
    # Rechenschritt 13:55:48..14:00:48 als laufend behandelt werden.
    lauf_status = ['GEPLANT'] * len(runs)
    lauf_start = [None] * len(runs)
    lauf_ende = [None] * len(runs)
    lauf_grund = [None] * len(runs)
    letzter_stop = None
    for i in range(horizon):
        raster_start = now + timedelta(minutes=i * dt_min)
        raster_ende = raster_start + timedelta(minutes=dt_min)
        grenzen = {raster_start, raster_ende}
        for an, aus in runs:
            if raster_start < an < raster_ende:
                grenzen.add(an)
            if raster_start < aus < raster_ende:
                grenzen.add(aus)
        grenzen = sorted(grenzen)
        wp_sample = 'STOP'
        for at, finish in zip(grenzen, grenzen[1:]):
            step_h = (finish - at).total_seconds() / 3600.0
            if step_h <= 0:
                continue
            # Reguläre Laufenden vor dem eventuellen naechsten Start erfassen.
            for j, (an, aus) in enumerate(runs):
                if lauf_status[j] == 'LAEUFT' and aus <= at:
                    lauf_status[j] = 'BEENDET'
                    lauf_ende[j] = aus
                    lauf_grund[j] = 'REGULAER_4H'
                    letzter_stop = aus if letzter_stop is None else max(letzter_stop, aus)
            # Ein Start wird erst ab dem exakten Startzeitpunkt wirksam.
            for j, (an, aus) in enumerate(runs):
                if lauf_status[j] != 'GEPLANT' or not (an <= at < aus):
                    continue
                if letzter_stop is not None and an < letzter_stop + timedelta(hours=p.bhkw_sperrzeit_h):
                    lauf_status[j] = 'GESPERRT'
                    lauf_grund[j] = 'NEUSTARTSPERRE_20MIN'
                else:
                    lauf_status[j] = 'LAEUFT'
                    lauf_start[j] = an
            active_index = next((j for j, (an, aus) in enumerate(runs)
                                 if lauf_status[j] == 'LAEUFT' and an <= at < aus), None)
            active = active_index is not None
            e_pv = []
            for field in REAL:
                val = pv_energy(field, at, step_h, pv_state, fallback)
                if val is None:
                    fehlende.add(field + '/' + at.date().isoformat())
                    val = 0.0
                e_pv.append(val)
            solar_e = fenster_energy(at, finish, pv_state, fallback)
            if solar_e is None:
                fehlende.add('Fenster/' + at.date().isoformat())
                solar_e = 0.0
            pv_kwh += sum(e_pv)
            sonne_kwh += solar_e
            if (t < p.solltemperatur_c - p.wp_hysterese_c and not active
                and (bat.eins_kwh + bat.zwei_kwh) > reserve_kwh + p.plan_wp_reserve_zuschlag_kwh):
                wp = 'HEIZEN'
            elif (kuehlen_erlaubt and t > p.maximum_c
                  and (bat.eins_kwh + bat.zwei_kwh) > reserve_kwh + p.plan_wp_reserve_zuschlag_kwh):
                wp = 'KUEHLEN'
            else:
                wp = 'STOP'
            wp_sample = wp
            el_load = p.basislast_kw + (p.wp_elektrisch_kw if wp != 'STOP' else 0)
            el_load += boost_kwh_im_intervall(boost, at, finish) / step_h
            short, spill, transfer, auto_stop, bhkw_anteil = batterie_schritt(
                bat, e_pv[0]/step_h, e_pv[1]/step_h,
                p.bhkw_elektrisch_kw if active else 0.0, el_load, step_h, p)
            if active and auto_stop:
                # Der Wirkanteil entspricht nur einem Teil des Schrittes.
                # Protokolliere den geschaetzten Abschaltzeitpunkt, nicht
                # faelschlich den Anfang des Fuenf-Minuten-Rasters.
                stop_zeit = at + (finish - at) * max(0.0, min(1.0, bhkw_anteil))
                abgeschaltete_laeufe.add(active_index)
                lauf_status[active_index] = 'BEENDET'
                lauf_ende[active_index] = stop_zeit
                lauf_grund[active_index] = 'AKKU_VOLL_GESCHAETZT'
                letzter_stop = stop_zeit if letzter_stop is None else max(letzter_stop, stop_zeit)
                abschaltungen.append(stop_zeit.isoformat(timespec='seconds'))
            mangel += short
            ueberschuss += spill
            if wp != 'STOP':
                wp_kwh += p.wp_elektrisch_kw * step_h
            t = temperatur_nach_schritt(t, ta, active, wp, step_h, p,
                                        solar_kw=solar_e/step_h, bhkw_anteil=bhkw_anteil)
            min_t = min(t, min_t)
            max_t = max(t, max_t)
            min_b1 = min(min_b1, bat.eins_kwh)
            min_energy = min(min_energy, bat.eins_kwh + bat.zwei_kwh)
        if i % 12 == 0:
            sample.append({'zeit': raster_start.isoformat(timespec='minutes'),
                           'temperatur_c': round(t, 2),
                           'akku_kwh': round(bat.eins_kwh + bat.zwei_kwh, 2),
                           'wp': wp_sample})
    # Ein Lauf kann am Ende des 24h-Horizonts noch aktiv sein.
    laufprotokoll = []
    for j, (an, aus) in enumerate(runs):
        laufprotokoll.append({
            'plan_start': an.isoformat(timespec='seconds'),
            'plan_ende': aus.isoformat(timespec='seconds'),
            'ist_start': lauf_start[j].isoformat(timespec='seconds') if lauf_start[j] else None,
            'ist_ende': lauf_ende[j].isoformat(timespec='seconds') if lauf_ende[j] else None,
            'status': lauf_status[j], 'grund': lauf_grund[j]})
    reasons = []
    if fehlende:
        reasons.append('Prognose fehlt: ' + ', '.join(sorted(fehlende)))
    # Die Gesamtreserve ist ein Zielwert am ENDE des Planungshorizonts.
    # Ein anfangs niedriger SOC soll einen Wiederaufladeplan nicht ausschliessen.
    end_energy = bat.eins_kwh + bat.zwei_kwh
    if end_energy < reserve_kwh - 1e-6:
        reasons.append('Gesamtreserve am Planende unterschritten')
    if min_b1 < p.plan_inselbatterie_min_kwh:
        reasons.append('Inselbatterie unter %.2f kWh' % p.plan_inselbatterie_min_kwh)
    if mangel > 0.01:
        reasons.append('Elektrischer Fehlbetrag')
    if min_t < p.minimum_c - 0.01:
        reasons.append('Raumtemperatur unter Komfortgrenze')
    if max_t > p.maximum_c + 0.01:
        reasons.append('Raumtemperatur ueber Komfortgrenze')
    return {
        'anzahl': plan, 'startzeiten_im_horizont': [v.isoformat(timespec='minutes') for v in starts],
        'zulaessig': not reasons, 'gruende': reasons,
        'startanzahl_im_horizont': len(starts),
        'minimum_soc_prozent': round(min_energy/(p.batterie1_kwh + p.batterie2_kwh)*100, 1),
        'minimum_gesamt_kwh': round(min_energy, 2), 'minimum_inselbatterie_kwh': round(min_b1, 2),
        'temperatur_min_c': round(min_t, 2), 'temperatur_max_c': round(max_t, 2),
        'temperatur_ende_c': round(t, 2), 'energie_ende_kwh': round(end_energy, 2),
        'soc_ende_prozent': round(end_energy / (p.batterie1_kwh + p.batterie2_kwh) * 100, 1),
        'elektrischer_fehlbetrag_kwh': round(mangel, 3),
        'pv_im_horizont_kwh': round(pv_kwh, 2), 'fensterwaerme_im_horizont_kwh': round(sonne_kwh, 2),
        'wp_strom_kwh': round(wp_kwh, 2), 'pv_ueberschuss_kwh': round(ueberschuss, 2),
        'kuehlung_im_plan': False if runs else True,
        'bhkw_abschaltung_geschaetzt': abschaltungen, 'bhkw_laeufe': laufprotokoll,
        'ersatzprofile': sorted(fallback), 'verlauf_stuendlich': sample}


def plan_ermitteln(cache, pv, p=None, zeit=None, boost=None):
    p = p or Parameter()
    zeit = (zeit or datetime.now(TZ)).astimezone(TZ)
    result = {'zeitpunkt': zeit.isoformat(timespec='seconds'), 'horizont_h': 24,
              'modus': 'PLANUNGSSIMULATION – KEINE AKTORBEFEHLE',
              'automatische_aktorbefehle': False, 'auswahl': None, 'szenarien': [],
              'boost': boost}
    missing = [name for name in ('TM_B', 'IN_W', 'BM_W', 'MO_W') if cache.veraltet(name, p.sensor_max_alter_s)]
    if missing:
        result.update(status='DATEN FEHLEN', gruende=['Sensor fehlt/veraltet: ' + ', '.join(missing)])
        return result
    soc = cache.gesamt_soc(p.sensor_max_alter_s)
    if not soc['gueltig']:
        result.update(status='BATTERIEDATEN UNGUELTIG', gruende=[soc.get('grund')])
        return result
    tm = cache._werte['TM_B'][0]
    motor = cache._werte['MO_W'][0]
    b1 = p.batterie1_kwh - cache._werte['IN_W'][0].entnommen_kwh
    b2 = p.batterie2_kwh - cache._werte['BM_W'][0].entnommen_kwh
    pv_state = pv.status()
    pv_state['zeitverlauf_wh'] = pv.state.get('zeitverlauf_wh', {})
    for n in sorted(STARTPLAENE):
        result['szenarien'].append(simuliere_plan(n, zeit, tm.innen_c, tm.aussen_c,
                                                  b1, b2, motor, pv_state, p, boost=boost))
    admissible = [e for e in result['szenarien'] if e['zulaessig']]
    if admissible:
        best = min(admissible, key=lambda e: (e['anzahl'], e['pv_ueberschuss_kwh']))
        result['auswahl'] = {k: v for k, v in best.items() if k != 'verlauf_stuendlich'}
        result['status'] = 'SIMULIERTER PLAN GEFUNDEN'
        result['begruendung'] = 'Kleinste Startanzahl, die die modellierten Energie-/Temperaturgrenzen einhaelt.'
    else:
        # Wenn allein die untere Komfortgrenze verfehlt wird, ist 'kein Plan'
        # fuer ein Inselhaus keine hilfreiche Reaktion. Das BHKW soll dann
        # moeglichst viel heizen, solange KEINE andere Modellgrenze verletzt wird.
        # Die Verletzung der Mindesttemperatur bleibt ausdruecklich sichtbar.
        thermische_notplaene = [e for e in result['szenarien']
            if e.get('anzahl', 0) > 0
            and e.get('gruende') == ['Raumtemperatur unter Komfortgrenze']]
        if thermische_notplaene:
            # Bei gegebenen festen Schemata die hoechste moegliche Startanzahl.
            # 'zulaessig' bleibt False: das Komfortziel wird NICHT erreicht.
            best = max(thermische_notplaene, key=lambda e: e['anzahl'])
            result['auswahl'] = {k: v for k, v in best.items() if k != 'verlauf_stuendlich'}
            result['auswahl']['automatisch_startbar'] = True
            result['auswahl']['heizdefizit_notplan'] = True
            result['status'] = 'HEIZDEFIZIT - BESTMOEGLICHER HEIZPLAN'
            result['warnung'] = ('Mindesttemperatur %.1f C wird unterschritten; '
                                 'Schema %d als Heizdefizit-Plan gewaehlt, '
                                 'andere Modellgrenzen eingehalten.'
                                 % (p.minimum_c, best['anzahl']))
            result['begruendung'] = ('Kein Schema erreicht die Mindesttemperatur durchgehend; '
                                    'hoechste Startanzahl unter Einhaltung aller '
                                    'anderen Modellgrenzen ausgewaehlt.')
        else:
            result['status'] = 'KEIN ZULAESSIGER PLAN'
            result['begruendung'] = 'Keine der sechs Varianten erfuellt alle modellierten Bedingungen.'
    result['hinweise'] = [
        'Keine Steuerfreigabe; bereits vergangene Starts werden nicht als wirklich ausgefuehrt angenommen.',
        'Konstante Aussentemperatur, Hausgrundlast %.0f W, WP-Zustand nicht gemessen.' % (p.basislast_kw * 1000),
        '800-W-Spannungseinspeiser nur angenaehert; Einzel-SOC weiterhin zu beachten.',
        'Bei BHKW-Schema keine Kuehlung; PV-Abregelung modelliert.',
        'BHKW-Abschaltung bei voller kleiner Batterie nur energetisch angenaehert, nicht anhand 53 V.',
        'Gesamtreserve gilt am Ende der 24 h; niedrige Zwischenwerte bleiben als Warnsignal sichtbar.',
        'Boost: elektrische Zusatzlast gleichmaessig bis zu seinem Ablauf verteilt.',
        'Ein Zeitplan kann sich bei laufender Messung und neuen Prognosen aendern.',
        'Plananzahl bezieht sich auf das feste Tagesschema; künftige Startanzahl im rollierenden Horizont kann abweichen.'
    ]
    return result
