# Offgrid-Regelung 1.0.5 – Lokaler WP-Status

Korrigiert den lokalen Befehl `python3 bhkw_lokal.py STATUS`: Die Antwort enthält jetzt zusätzlich den vollständigen Block `wp_automatik` aus `self.wp_auto.status()` mit unter anderem `wp_modus`, `wp_heizung_beauftragt` und `wp_kuehlung_beauftragt`. Keine Änderungen an Motorsteuerung, WP-Regelung oder Automatikfreigabe.

**Installation:** Nur `dienst.py` austauschen und `dienst.py` bei geeignetem Anlagenzustand neu starten. Der Datenordner bleibt erhalten. Der Neustart deaktiviert wie bisher die Automatik; gegebenenfalls mit `python3 bhkw_lokal.py AUTO_EIN` bewusst wieder freigeben. Danach `python3 bhkw_lokal.py STATUS` prüfen.

test