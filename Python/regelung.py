"""Offgrid-Regelung v0.1: Nur Simulation, keinerlei reale Schaltvorgänge."""
import argparse
from parameter import Parameter, Anfangszustand
from bhkw_planung import Prognose, waehle
from protokoll import speichern


def main():
    parser = argparse.ArgumentParser(description='Offgrid-Tagesplanung (Simulation)')
    parser.add_argument('--aussen', type=float, default=4.0)
    parser.add_argument('--pv1', type=float, default=8.0,
                        help='Tagesprognose 4-kWp-Feld, kWh (Beispielwert)')
    parser.add_argument('--pv2', type=float, default=5.0,
                        help='Tagesprognose 2,5-kWp-Feld, kWh (Beispielwert)')
    parser.add_argument('--innen', type=float, default=22.0)
    parser.add_argument('--batterie1', type=float, default=12.0, help='kWh, angenommener Wert')
    parser.add_argument('--batterie2', type=float, default=4.0, help='kWh, angenommener Wert')
    parser.add_argument('--vortag-starts', type=int, choices=range(6), default=0)
    parser.add_argument('--ausgabe', default='simulation.json')
    args = parser.parse_args()
    p = Parameter()
    anfang = Anfangszustand(args.innen, args.batterie1, args.batterie2, args.vortag_starts)
    prognose = Prognose(args.aussen, args.pv1, args.pv2)
    best, all_results, erlaubt = waehle(anfang, prognose, p)
    print('OFFGRID-SIMULATION – KEINE HARDWARESTEUERUNG')
    print('Alle PV-, Verbrauchs- und Batteriewerte sind Vorgaben/Annahmen.')
    for e in all_results:
        print(f'{e.starts} Starts: T={e.minimum_innen_c:.2f}..{e.spitze_innen_c:.2f} °C, '
              f'B1 min={e.minimum_batterie1_kwh:.2f} kWh, '
              f'B2 min={e.minimum_batterie2_kwh:.2f} kWh, '
              f'Strommangel={e.strommangel_kwh:.2f} kWh, '
              f'{"zulässig" if e.zulaessig(p) else "NICHT zulässig"}')
    if erlaubt:
        print(f'Simulationsvorschlag: {best.starts} Starts, Zeiten {best.zeiten}')
    else:
        print('WARNUNG: Kein zulässiger BHKW-Plan. Nur Diagnose, NICHT ausführbar!')
        print(f'Geringste modellierte Verletzung bei {best.starts} Starts')
    speichern(args.ausgabe, best)
    print('Details:', args.ausgabe)

if __name__ == '__main__':
    main()
