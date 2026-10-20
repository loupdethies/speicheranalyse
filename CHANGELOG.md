# Änderungsprotokoll

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [1.0.0] – 2026-10-20

Erste Veröffentlichung.

### Funktionen
- Scanner für beliebige Laufwerke (interne Platten, USB-Sticks) mit „Größe auf Datenträger“: Cluster-Rundung, komprimierte Dateien, Cloud-Platzhalter, Hardlinks nur einmal, Kleinstdateien in der NTFS-Dateitabelle.
- Einordnung in acht Kategorien (Betriebssystem, Programme, Eigene Dateien, Caches & Temp, Programmdaten, Entwicklung, System-reserviert, Sonstiges) über anpassbare Regeln in `kategorien.json`; eigene Dateien zusätzlich nach Dateityp.
- HTML-Bericht ohne Internetverbindung: Laufwerksbalken, Treemap nach Kategorie oder Ordner mit Drill-down, Dateitypen, größte Dateien, Hell-/Dunkelmodus.
- Abgleich mit den Windows-Werten: erklärt die Lücke zwischen „belegt“ und gescannten Dateien (NTFS-Verwaltungsdaten, App-Pakete, Wiederherstellungspunkte).
- Vergleich mit dem vorherigen Scan desselben Laufwerks: Veränderung je Kategorie und Ordner.
- Aufräumliste mit Einstufung und Anleitung; Regeln in `aufraeumen.json`. Das Tool löscht nichts.
- Lokale App (`app.py`): Laufwerk wählen, Analyse starten (optional mit Admin-Rechten), frühere Berichte öffnen.
