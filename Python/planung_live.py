"""Status der BHKW-Planung: keine automatische Freigabe ohne reale SoC-Daten."""
from datetime import datetime, timedelta
from parameter import STARTPLAENE, Parameter


def status_plan(pv_status, temperatur_gueltig, p=None):
    p = p or Parameter()
    jetzt = datetime.now().astimezone()
    heute = jetzt.date().isoformat()
    morgen = (jetzt.date() + timedelta(days=1)).isoformat()
    forecasts = pv_status.get('prognosen_kwh', {})
    felder = {}
    for name in ('pv1_4kwp_38grad', 'pv2_2_5kwp_20grad'):
        werte = forecasts.get(name, {})
        felder[name] = {'heute_kwh': werte.get(heute), 'morgen_kwh': werte.get(morgen)}
    fenster = pv_status.get('fenster_waerme')
    if fenster is None:
        from solar_waerme import fenster_waerme_tage
        fenster = fenster_waerme_tage(forecasts, jetzt.date())
    return {'fenster_waerme': fenster, 'fenster_halbstunden': pv_status.get('fenster_halbstunden', {}), 'status': 'NICHT FREIGEGEBEN (Simulation/Diagnose)',
            'automatische_aktorbefehle': False,
            'gruende': [
                'SoC ist aus SD/En bekannt, aber Last- und Ladungsprognose fehlen noch',
                'BHKW-Istzustand und letzter realer Start/Stop sind noch unbekannt',
                'Hauslast und Einspeiser-Spannungsregelung nicht kalibriert',
                'PV-Erzeugungsprognosen der realen Felder werden noch nicht zeitaufgeloest in eine Batteriebilanz umgerechnet'],
            'pv_tagesprognosen': felder,
            'temperatur_gueltig': temperatur_gueltig,
            'verfuegbare_startschemata': {str(k): [f'{int(s):02d}:{int(round(s%1*60)):02d}' for s in v]
                                        for k, v in STARTPLAENE.items()},
            'hinweis': 'Kein Startschema ist als fahrbarer Plan ausgewaehlt.'}
