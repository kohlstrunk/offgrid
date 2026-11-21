"""Einfaches Ein-Zonen-Hausmodell. Kellerwirkung ist implizit/noch unkalibriert."""
from parameter import Parameter


def temperatur_nach_schritt(innen_c: float, aussen_c: float,
                            bhkw_an: bool, wp_modus: str,
                            dauer_h: float, p: Parameter, solar_kw: float = 0.0, bhkw_anteil: float = 1.0) -> float:
    thermisch_kw = p.bhkw_thermisch_kw * bhkw_anteil if bhkw_an else 0.0
    if wp_modus == 'HEIZEN':
        thermisch_kw += p.wp_heizen_kw
    elif wp_modus == 'KUEHLEN':
        thermisch_kw -= p.wp_kuehlen_kw
    thermisch_kw += solar_kw
    verlust_kw = p.waermeverlust_kw_pro_k * (innen_c - aussen_c)
    return innen_c + (thermisch_kw - verlust_kw) * dauer_h / p.hauskapazitaet_kwh_pro_k
