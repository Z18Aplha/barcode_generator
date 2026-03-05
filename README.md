# EAN-13 Barcode Generator

Python-CLI zum Erzeugen von EAN-13-Barcodes als `SVG` oder `PNG`.

Features:
- EAN-Validierung (12-stellig mit Prüfziffer-Berechnung, 13-stellig mit Prüfziffer-Check)
- Farben für Barcode/Text/Hintergrund
- Transparenter Hintergrund
- Steuerung von Breite, Höhe und Aspect Ratio
- Text unter dem Barcode (optional, mit Font-Steuerung)
- Optional: Schrift ins SVG einbetten oder Text in Pfade umwandeln (Canva-stabil)
- Config-Sets speichern/laden

Dateiname der Ausgabe:
- `barcode_<ean13>.svg` oder `barcode_<ean13>.png`

## Voraussetzungen

- Python 3.10+
- Pakete:
  - `python-barcode`
  - `Pillow`
  - optional für `--text-to-path`: `fonttools`

Installation:

```bash
pip install python-barcode Pillow fonttools
```

## Nutzung

```bash
python barcode-generator.py <ean> [optionen]
```

Beispiel:

```bash
python barcode-generator.py 400638133393 --background transparent --foreground fffde7
```

Hinweis: Bei 12 Ziffern wird die Prüfziffer ergänzt.  
Obiges Beispiel erzeugt `barcode_4006381333931.svg` (standardmäßig SVG).

## CLI-Optionen (mit Defaults)

- `ean` (pflicht, positional): 12 oder 13 Ziffern
- `--load-config` (Default: `None`): lädt ein Config-Set
- `--save-config` (Default: `None`): speichert das effektive Config-Set
- `--config-dir` (Default: `.barcode-generator-configs`)
- `--output-format` (Default: `svg`, Werte: `svg|png`)
- `--foreground` (Default: `#000000`)
- `--background` (Default: `#FFFFFF`, zusätzlich `transparent|none`)
- `--width-px` (Default: `None`)
- `--height-px` (Default: `None`)
- `--aspect-ratio` (Default: `None`, Breite/Höhe)
- `--no-text` (Default: `False`)
- `--text-layout` (Default: `ean-grouped`, Werte: `ean-grouped|single`)
- `--font-family` (Default: `OCR-B, OCRB, monospace`)
- `--font-size` (Default: `10.0`)
- `--text-color` (Default: `None`, dann wie `--foreground`)
- `--leading-digit-offset` (Default: `0.0`)
- `--text-y-offset` (Default: `1.0`)
- `--embed-font-file` (Default: `None`, `.ttf`/`.otf`)
- `--text-to-path` (Default: `False`)
- `--text-to-path-font-file` (Default: `None`)
- `--output-dir` (Default: `.`)

## Größenverhalten

- Wenn `--width-px` und `--height-px` gesetzt sind: genau diese Canvas-Größe.
- Wenn nur eine Dimension gesetzt ist: die andere wird aus dem Content-Verhältnis berechnet.
- Wenn keine gesetzt ist:
  - SVG nutzt eine enge Canvas um den Inhalt (mit kleinem Padding).
  - PNG nutzt Standardbreite `1000px` (wenn `--width-px` nicht gesetzt ist), Höhe proportional.
- `--aspect-ratio` verändert die Barcode-Balken-Geometrie (breiter/flacher bzw. schmaler/höher), nicht nur den Container.

## Farben

Erlaubt:
- Hex: `fff`, `#fff`, `fffde7`, `#fffde7`
- Namen: `white`, `black`
- Nur für Hintergrund zusätzlich: `transparent` / `none`

Fehlerfall:
- Wenn `--foreground` und `--background` identisch sind (nicht transparent), wird abgebrochen.

## Text unter dem Barcode

- Text ist standardmäßig aktiv.
- Mit `--no-text` wird er deaktiviert.
- Der Text wird gleichmäßig über die Barcode-Breite gespannt (`textLength` + `lengthAdjust=spacing` im SVG).
- Abstand ist als Abstand **von Balkenunterkante zu Textoberkante** definiert (`--text-y-offset`).

## Font-Einbettung / Text als Pfad

- `--embed-font-file <font.ttf|font.otf>`:
  - bettet den Font in das SVG ein (`@font-face`), damit Clients konsistenter rendern.
- `--text-to-path`:
  - wandelt Text in SVG-Pfade um (keine Client-Schrift nötig, stabil in Canva etc.).
  - nutzt automatisch eine passende Systemschrift aus `--font-family`, oder explizit:
    - `--text-to-path-font-file <font.ttf|font.otf>`
    - alternativ `--embed-font-file` als Quelle.

Hinweis:
- `--text-to-path` wirkt nur bei `--output-format svg`.

## Config speichern / laden

Config speichern:

```bash
python barcode-generator.py 400638133393 --foreground fffde7 --background transparent --save-config packaging
```

Config laden:

```bash
python barcode-generator.py 400638133393 --load-config packaging
```

Details:
- Dateipfad: `<config-dir>/<name>.json` (Suffix `.json` wird bei Bedarf ergänzt)
- Beim Laden überschreiben explizit gesetzte CLI-Args die geladenen Werte.
- Gespeichert werden die effektiven Einstellungen inkl. Defaults.

## PNG-Ausgabe

```bash
python barcode-generator.py 400638133393 --output-format png
python barcode-generator.py 400638133393 --output-format png --width-px 1600
python barcode-generator.py 400638133393 --output-format png --width-px 1600 --height-px 500
```

- PNG wird aus dem finalen SVG gerendert.
- Standardbreite ohne `--width-px`: `1000px`.

## Exit Codes

- `0`: Erfolg
- `2`: Eingabe-/Validierungsfehler (z. B. ungültige EAN, Farbe, Argumentwerte)
- `1`: sonstiger Laufzeitfehler

## Ausgabe

Im Erfolgsfall schreibt das Skript den absoluten Pfad der erzeugten Datei auf `stdout`.

## WebApp (Streamlit)

Zusätzlich gibt es jetzt eine lokale WebApp mit Live-Vorschau:
- UI mit Basis- und Erweitert-Bereich
- Sofortige Vorschau bei jeder Änderung
- Download als SVG oder PNG
- Batch-Download als ZIP (mehrere EANs, komma- oder zeilengetrennt)
- Laden/Speichern von Config-Sets aus `.barcode-generator-configs`

Start lokal (ohne Docker):

```bash
pip install -r requirements.txt
streamlit run webapp/app.py
```

Danach im Browser:
- `http://localhost:8501`

### WebApp mit Docker

Build + Start:

```bash
docker compose up --build
```

Danach im Browser:
- `http://localhost:8501`

Hinweis:
- Die Configs werden per Volume gemountet:
  - `./.barcode-generator-configs:/app/.barcode-generator-configs`
- OCR-B ist im Repo unter `fonts/OCRB.otf` enthalten und wird im Container nach
  `/usr/local/share/fonts/custom/OCRB.otf` installiert.
- Für stabile SVG-Textpfade im Container kannst du z. B. setzen:
  - `--text-to-path --text-to-path-font-file /usr/local/share/fonts/custom/OCRB.otf`
