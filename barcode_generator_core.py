#!/usr/bin/env python3
import argparse
import base64
import functools
import io
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from barcode import EAN13
from barcode.writer import SVGWriter
from PIL import Image, ImageColor, ImageDraw, ImageFont

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

HEX_COLOR_RE = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
DIMENSION_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*(px|pt|pc|mm|cm|in)?\s*$")

UNIT_TO_PX = {
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "mm": 96.0 / 25.4,
    "cm": 96.0 / 2.54,
    "in": 96.0,
}
UNIT_TO_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0,
}
NAMED_TO_HEX = {
    "white": "#ffffff",
    "black": "#000000",
}
TEXT_ASCENT_FACTOR = 0.8
GENERIC_FONT_FALLBACKS = {
    "monospace": [
        "consola.ttf",
        "lucon.ttf",
        "cour.ttf",
        "courbd.ttf",
        "DejaVuSansMono.ttf",
        "LiberationMono-Regular.ttf",
        "NotoSansMono-Regular.ttf",
    ],
    "sansserif": [
        "arial.ttf",
        "segoeui.ttf",
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
        "NotoSans-Regular.ttf",
    ],
    "serif": [
        "times.ttf",
        "timesbd.ttf",
        "DejaVuSerif.ttf",
        "LiberationSerif-Regular.ttf",
        "NotoSerif-Regular.ttf",
    ],
}
FAMILY_FILENAME_HINTS = {
    "ocrb": ["ocrb.ttf", "ocr-b.ttf", "ocrb10bt.ttf", "ocrbstd.otf"],
    "arial": ["arial.ttf"],
    "segoeui": ["segoeui.ttf"],
}
DEFAULT_CONFIG_DIR = ".barcode-generator-configs"


def compute_ean13_checksum(ean12: str) -> str:
    total = 0
    for index, char in enumerate(ean12):
        digit = int(char)
        total += digit if index % 2 == 0 else digit * 3
    return str((10 - (total % 10)) % 10)


def normalize_ean(ean: str) -> str:
    if not ean.isdigit():
        raise ValueError("EAN muss nur aus Ziffern bestehen.")
    if len(ean) == 12:
        return ean + compute_ean13_checksum(ean)
    if len(ean) == 13:
        expected = compute_ean13_checksum(ean[:12])
        if ean[-1] != expected:
            raise ValueError(
                f"Ungültige EAN-13 Prüfziffer: erwartet {expected}, erhalten {ean[-1]}."
            )
        return ean
    raise ValueError("EAN muss genau 12 oder 13 Ziffern lang sein.")


def validate_color(value: str, allow_transparent: bool = True) -> str:
    cleaned = value.strip()
    lowered = cleaned.lower()
    if allow_transparent and lowered in {"transparent", "none"}:
        return "transparent"
    if HEX_COLOR_RE.match(cleaned):
        return f"#{cleaned.lstrip('#').lower()}"
    if lowered in NAMED_TO_HEX:
        return NAMED_TO_HEX.get(lowered, cleaned)
    raise ValueError(f"Ungültiger Farbwert: {value}")


def _parse_dimension(value: str, default_unit: str) -> tuple[float, str]:
    match = DIMENSION_RE.match(value)
    if not match:
        raise ValueError(f"Ungültige SVG-Dimension: {value}")
    amount = float(match.group(1))
    unit = match.group(2) or default_unit
    return amount, unit


def _to_px(value: str) -> float:
    amount, unit = _parse_dimension(value, default_unit="px")
    return amount * UNIT_TO_PX[unit]


def _to_mm(value: str) -> float:
    amount, unit = _parse_dimension(value, default_unit="mm")
    return amount * UNIT_TO_MM[unit]


def parse_svg_dimensions(svg_text: str) -> tuple[float, float]:
    root = ET.fromstring(svg_text)
    width_attr = root.attrib.get("width")
    height_attr = root.attrib.get("height")

    if width_attr and height_attr:
        return _to_px(width_attr), _to_px(height_attr)

    view_box = root.attrib.get("viewBox")
    if not view_box:
        raise ValueError("SVG enthält weder width/height noch viewBox.")
    parts = view_box.replace(",", " ").split()
    if len(parts) != 4:
        raise ValueError("Ungültige viewBox im SVG.")
    return float(parts[2]), float(parts[3])


def resolve_target_size(
    intrinsic_width: float,
    intrinsic_height: float,
    width_px: int | None,
    height_px: int | None,
    aspect_ratio: float | None,
) -> tuple[int, int]:
    ratio = intrinsic_width / intrinsic_height
    if ratio <= 0:
        raise ValueError("Seitenverhältnis muss größer als 0 sein.")

    if width_px is not None and height_px is not None:
        return width_px, height_px
    if width_px is not None:
        return width_px, max(1, round(width_px / ratio))
    if height_px is not None:
        return max(1, round(height_px * ratio)), height_px

    default_width = 450
    return default_width, max(1, round(default_width / ratio))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Erzeuge EAN-13 Barcodes als SVG.")
    parser.add_argument("ean", help="12 oder 13-stellige EAN-Nummer.")
    parser.add_argument(
        "--load-config",
        default=None,
        help="Lädt ein gespeichertes Config-Set (JSON) anhand des Namens.",
    )
    parser.add_argument(
        "--save-config",
        default=None,
        help="Speichert das aktuelle effektive Config-Set unter diesem Namen.",
    )
    parser.add_argument(
        "--config-dir",
        default=DEFAULT_CONFIG_DIR,
        help="Ordner für --save-config/--load-config.",
    )
    parser.add_argument(
        "--output-format",
        choices=("svg", "png"),
        default="svg",
        help="Ausgabeformat (Default: svg).",
    )
    parser.add_argument("--foreground", default="#000000", help="Barcode-Farbe.")
    parser.add_argument(
        "--background",
        default="#FFFFFF",
        help='Hintergrundfarbe oder "transparent"/"none".',
    )
    parser.add_argument("--width-px", type=int, default=None, help="Zielbreite in Pixel.")
    parser.add_argument("--height-px", type=int, default=None, help="Zielhöhe in Pixel.")
    parser.add_argument(
        "--aspect-ratio",
        type=float,
        default=None,
        help="Optionales Seitenverhältnis (Breite/Höhe).",
    )
    parser.add_argument(
        "--no-text",
        action="store_true",
        help="Deaktiviert die Klarschrift unter dem Barcode.",
    )
    parser.add_argument(
        "--text-layout",
        choices=("ean-grouped", "single"),
        default="ean-grouped",
        help="Textlayout unter dem Barcode.",
    )
    parser.add_argument(
        "--font-family",
        default="OCR-B, OCRB, monospace",
        help="Font-Familie für Text unter dem Barcode.",
    )
    parser.add_argument(
        "--font-size",
        type=float,
        default=10.0,
        help="Schriftgröße für Text unter dem Barcode (px; intern mm-basiert ausgegeben).",
    )
    parser.add_argument(
        "--text-color",
        default=None,
        help="Optional eigene Textfarbe (Default: wie --foreground).",
    )
    parser.add_argument(
        "--leading-digit-offset",
        type=float,
        default=0.0,
        help="Offset der ersten Ziffer in Modulbreiten relativ zum linken Rand des Barcode-Balkenbereichs.",
    )
    parser.add_argument(
        "--text-y-offset",
        type=float,
        default=1.0,
        help="Abstand zwischen Balkenunterkante und Textoberkante in Modulbreiten.",
    )
    parser.add_argument(
        "--embed-font-file",
        default=None,
        help="Optionaler Pfad zu TTF/OTF, wird direkt in das SVG eingebettet.",
    )
    parser.add_argument(
        "--text-to-path",
        action="store_true",
        help="Wandelt Text in SVG-Pfade um (Canva-stabil, keine Client-Fonts nötig).",
    )
    parser.add_argument(
        "--text-to-path-font-file",
        default=None,
        help="TTF/OTF für --text-to-path. Fallback: --embed-font-file.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Ausgabeverzeichnis (Default: aktuelles Verzeichnis).",
    )
    return parser


def _parse_style(style: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def _format_style(style_map: dict[str, str]) -> str:
    return ";".join(f"{key}:{value}" for key, value in style_map.items())


def make_background_transparent(root: ET.Element) -> None:
    background_rect = None
    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        if element.attrib.get("width") == "100%" and element.attrib.get("height") == "100%":
            background_rect = element
            break

    if background_rect is not None:
        style_map = _parse_style(background_rect.attrib.get("style", ""))
        style_map["fill"] = "none"
        background_rect.set("style", _format_style(style_map))
        background_rect.set("fill", "none")

    root_style = _parse_style(root.attrib.get("style", ""))
    root_style["background-color"] = "transparent"
    root.set("style", _format_style(root_style))


def _find_bar_metrics_mm(root: ET.Element) -> tuple[float, float, float, float]:
    bars: list[tuple[float, float, float, float]] = []

    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        if element.attrib.get("width") == "100%" and element.attrib.get("height") == "100%":
            continue
        try:
            x = _to_mm(element.attrib["x"])
            y = _to_mm(element.attrib["y"])
            width = _to_mm(element.attrib["width"])
            height = _to_mm(element.attrib["height"])
        except (KeyError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        bars.append((x, y, width, height))

    if not bars:
        raise ValueError("Konnte keine Barcode-Balken im SVG finden.")

    module_width = min(bar[2] for bar in bars)
    x_min = min(bar[0] for bar in bars)
    x_max = max(bar[0] + bar[2] for bar in bars)
    y_bottom = max(bar[1] + bar[3] for bar in bars)
    return module_width, x_min, x_max, y_bottom


def _find_barcode_group(root: ET.Element) -> ET.Element:
    for element in root.iter():
        if element.tag.endswith("g") and element.attrib.get("id") == "barcode_group":
            return element
    return root


def _remove_text_nodes(element: ET.Element) -> None:
    for child in list(element):
        if child.tag.endswith("text"):
            element.remove(child)
            continue
        _remove_text_nodes(child)


def _find_reference_text_y_mm(root: ET.Element) -> float | None:
    for element in root.iter():
        if not element.tag.endswith("text"):
            continue
        y_value = element.attrib.get("y")
        if not y_value:
            continue
        try:
            return _to_mm(y_value)
        except ValueError:
            continue
    return None


def _add_text_node(
    parent: ET.Element,
    text: str,
    x_mm: float,
    y_mm: float,
    color: str,
    font_size: float,
    font_family: str,
    anchor: str = "middle",
    text_length_mm: float | None = None,
) -> None:
    text_el = ET.SubElement(parent, f"{{{SVG_NS}}}text")
    text_el.set("x", f"{x_mm:.3f}mm")
    text_el.set("y", f"{y_mm:.3f}mm")
    font_size_mm = font_size * UNIT_TO_MM["px"]
    style = (
        f"fill:{color};font-size:{font_size_mm:.3f}mm;"
        f"text-anchor:{anchor};font-family:{font_family};"
    )
    text_el.set("style", style)
    if text_length_mm is not None:
        text_el.set("textLength", f"{text_length_mm:.3f}mm")
        text_el.set("lengthAdjust", "spacing")
    text_el.text = text


def _inject_embedded_font(root: ET.Element, font_file: Path, font_family_name: str) -> None:
    ext = font_file.suffix.lower()
    if ext not in {".ttf", ".otf"}:
        raise ValueError("--embed-font-file muss auf .ttf oder .otf enden.")
    font_bytes = font_file.read_bytes()
    encoded = base64.b64encode(font_bytes).decode("ascii")
    mime = "font/ttf" if ext == ".ttf" else "font/otf"
    fmt = "truetype" if ext == ".ttf" else "opentype"

    defs = ET.Element(f"{{{SVG_NS}}}defs")
    style = ET.SubElement(defs, f"{{{SVG_NS}}}style")
    style.text = (
        f"@font-face{{font-family:'{font_family_name}';"
        f"src:url(data:{mime};base64,{encoded}) format('{fmt}');}}"
    )
    root.insert(0, defs)


def _normalize_units_to_px(root: ET.Element) -> None:
    for element in root.iter():
        if element.tag.endswith("rect"):
            for key in ("x", "y", "width", "height"):
                value = element.attrib.get(key)
                if not value or value.endswith("%"):
                    continue
                element.set(key, f"{_to_px(value):.3f}")

        if element.tag.endswith("text"):
            for key in ("x", "y", "textLength"):
                value = element.attrib.get(key)
                if not value:
                    continue
                element.set(key, f"{_to_px(value):.3f}")

            style = _parse_style(element.attrib.get("style", ""))
            font_size = style.get("font-size")
            if font_size:
                style["font-size"] = f"{_to_px(font_size):.3f}px"
                element.set("style", _format_style(style))


def _find_bar_bounds_px(root: ET.Element) -> tuple[float, float, float, float, float]:
    bars: list[tuple[float, float, float, float]] = []
    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        if element.attrib.get("width") == "100%" and element.attrib.get("height") == "100%":
            continue
        try:
            x = float(element.attrib["x"])
            y = float(element.attrib["y"])
            width = float(element.attrib["width"])
            height = float(element.attrib["height"])
        except (KeyError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        bars.append((x, y, width, height))

    if not bars:
        raise ValueError("Konnte keine Barcode-Balken im SVG finden.")

    x_min = min(item[0] for item in bars)
    y_min = min(item[1] for item in bars)
    x_max = max(item[0] + item[2] for item in bars)
    y_max = max(item[1] + item[3] for item in bars)
    module_width = (x_max - x_min) / 95.0
    return x_min, y_min, x_max, y_max, module_width


def _estimate_text_bounds_px(root: ET.Element) -> tuple[float, float, float, float] | None:
    bounds: list[tuple[float, float, float, float]] = []
    for element in root.iter():
        if not element.tag.endswith("text"):
            continue
        try:
            x = float(element.attrib["x"])
            y = float(element.attrib["y"])
        except (KeyError, ValueError):
            continue

        style = _parse_style(element.attrib.get("style", ""))
        anchor = style.get("text-anchor", "start")
        font_size = float(style.get("font-size", "10").replace("px", ""))
        if "textLength" in element.attrib:
            width = float(element.attrib["textLength"])
        else:
            width = max(1.0, len(element.text or "") * font_size * 0.62)

        if anchor == "middle":
            x0, x1 = x - (width / 2.0), x + (width / 2.0)
        elif anchor == "end":
            x0, x1 = x - width, x
        else:
            x0, x1 = x, x + width

        y0 = y - (TEXT_ASCENT_FACTOR * font_size)
        y1 = y + (0.25 * font_size)
        bounds.append((x0, y0, x1, y1))

    if not bounds:
        return None

    return (
        min(item[0] for item in bounds),
        min(item[1] for item in bounds),
        max(item[2] for item in bounds),
        max(item[3] for item in bounds),
    )


def _resolve_canvas_size(
    content_width_px: float,
    content_height_px: float,
    width_px: int | None,
    height_px: int | None,
) -> tuple[int, int]:
    ratio = content_width_px / content_height_px
    if width_px is not None and height_px is not None:
        return width_px, height_px
    if width_px is not None:
        return width_px, max(1, round(width_px / ratio))
    if height_px is not None:
        return max(1, round(height_px * ratio)), height_px
    return max(1, round(content_width_px)), max(1, round(content_height_px))


def _set_tight_viewbox_and_canvas(
    root: ET.Element,
    requested_width_px: int | None,
    requested_height_px: int | None,
) -> tuple[int, int]:
    x_min, y_min, x_max, y_max, module = _find_bar_bounds_px(root)
    text_bounds = _estimate_text_bounds_px(root)

    content_top = y_min
    content_bottom = y_max
    if text_bounds is not None:
        content_top = min(content_top, text_bounds[1])
        content_bottom = max(content_bottom, text_bounds[3])

    pad_x = max(4.0, module * 2.0)
    pad_top = max(4.0, module * 1.5)
    pad_bottom = max(6.0, module * 2.0)

    view_x = x_min - pad_x
    view_w = (x_max - x_min) + (2.0 * pad_x)
    view_y = content_top - pad_top
    view_h = (content_bottom - content_top) + pad_top + pad_bottom

    width_px, height_px = _resolve_canvas_size(
        content_width_px=view_w,
        content_height_px=view_h,
        width_px=requested_width_px,
        height_px=requested_height_px,
    )

    root.set("viewBox", f"{view_x:.3f} {view_y:.3f} {view_w:.3f} {view_h:.3f}")
    root.set("width", f"{width_px}px")
    root.set("height", f"{height_px}px")
    return width_px, height_px


def _normalize_family_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _candidate_font_dirs() -> list[Path]:
    dirs: list[Path] = [Path(__file__).resolve().parent / "fonts"]
    if sys.platform.startswith("win"):
        windir = Path(
            Path.home().drive + "\\Windows"
            if Path.home().drive
            else "C:\\Windows"
        )
        dirs.append(windir / "Fonts")
    elif sys.platform == "darwin":
        dirs.extend(
            [
                Path("/System/Library/Fonts"),
                Path("/Library/Fonts"),
                Path.home() / "Library" / "Fonts",
            ]
        )
    else:
        dirs.extend(
            [
                Path("/usr/share/fonts"),
                Path("/usr/local/share/fonts"),
                Path.home() / ".fonts",
                Path.home() / ".local" / "share" / "fonts",
            ]
        )
    return [d for d in dirs if d.exists()]


@functools.lru_cache(maxsize=1)
def _system_font_files() -> list[Path]:
    files: list[Path] = []
    for font_dir in _candidate_font_dirs():
        for pattern in ("*.ttf", "*.otf"):
            files.extend(font_dir.rglob(pattern))
    return files


def _find_font_by_filename(candidates: list[str]) -> Path | None:
    wanted = {name.lower() for name in candidates}
    for path in _system_font_files():
        if path.name.lower() in wanted:
            return path
    return None


def _discover_system_font_file(font_family: str) -> Path | None:
    families = [part.strip().strip("'\"") for part in font_family.split(",")]
    families = [name for name in families if name]
    font_files = _system_font_files()
    if not font_files:
        return None

    indexed: list[tuple[str, Path]] = [
        (_normalize_family_key(path.stem), path) for path in font_files
    ]

    for family in families:
        key = _normalize_family_key(family)
        if not key:
            continue

        hints = FAMILY_FILENAME_HINTS.get(key)
        if hints:
            hit = _find_font_by_filename(hints)
            if hit is not None:
                return hit

        if key in GENERIC_FONT_FALLBACKS:
            hit = _find_font_by_filename(GENERIC_FONT_FALLBACKS[key])
            if hit is not None:
                return hit

        exact = [path for stem, path in indexed if stem == key]
        if exact:
            return exact[0]

        starts = [path for stem, path in indexed if stem.startswith(key)]
        if starts:
            return starts[0]

        contains = [path for stem, path in indexed if key in stem]
        if contains:
            return contains[0]

    return None


def _replace_text_with_paths(root: ET.Element, font_file: Path) -> None:
    try:
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.ttLib import TTFont
    except Exception as exc:
        raise ValueError(
            "--text-to-path benötigt fontTools. Installiere es mit: pip install fonttools"
        ) from exc

    font = TTFont(str(font_file))
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"].metrics
    units_per_em = float(font["head"].unitsPerEm)
    fallback_glyph = ".notdef" if ".notdef" in hmtx else next(iter(hmtx))

    parent_map = {child: parent for parent in root.iter() for child in parent}
    text_nodes = [el for el in root.iter() if el.tag.endswith("text")]

    for text_el in text_nodes:
        parent = parent_map.get(text_el)
        if parent is None:
            continue

        text = text_el.text or ""
        if not text:
            parent.remove(text_el)
            continue

        style = _parse_style(text_el.attrib.get("style", ""))
        anchor = style.get("text-anchor", "start")
        fill = style.get("fill", "#000000")
        fill_opacity = style.get("fill-opacity")
        font_size_px = float(style.get("font-size", "10").replace("px", "").strip() or "10")
        x = float(text_el.attrib.get("x", "0"))
        y = float(text_el.attrib.get("y", "0"))

        glyphs: list[tuple[str, float]] = []
        total_advance = 0.0
        for ch in text:
            glyph_name = cmap.get(ord(ch))
            if glyph_name is None and ch == " " and "space" in hmtx:
                glyph_name = "space"
            if glyph_name is None or glyph_name not in hmtx:
                glyph_name = fallback_glyph
            advance = float(hmtx[glyph_name][0])
            glyphs.append((glyph_name, advance))
            total_advance += advance

        scale = font_size_px / units_per_em
        natural_width = total_advance * scale
        target_width = (
            float(text_el.attrib["textLength"]) if "textLength" in text_el.attrib else natural_width
        )
        render_width = max(0.0, target_width)

        extra_spacing = 0.0
        if len(glyphs) > 1:
            extra_spacing = (render_width - natural_width) / (len(glyphs) - 1)

        if anchor == "middle":
            cursor_x = x - (render_width / 2.0)
        elif anchor == "end":
            cursor_x = x - render_width
        else:
            cursor_x = x

        group = ET.Element(f"{{{SVG_NS}}}g")
        group.set("fill", fill)
        if fill_opacity is not None:
            group.set("fill-opacity", fill_opacity)

        for idx, (glyph_name, advance) in enumerate(glyphs):
            if glyph_name != "space":
                pen = SVGPathPen(glyph_set)
                glyph_set[glyph_name].draw(pen)
                commands = pen.getCommands()
                if commands:
                    path = ET.SubElement(group, f"{{{SVG_NS}}}path")
                    path.set("d", commands)
                    path.set(
                        "transform",
                        (
                            f"translate({cursor_x:.3f},{y:.3f}) "
                            f"scale({scale:.6f},{-scale:.6f})"
                        ),
                    )

            cursor_x += advance * scale
            if idx < len(glyphs) - 1:
                cursor_x += extra_spacing

        insert_index = list(parent).index(text_el)
        parent.remove(text_el)
        parent.insert(insert_index, group)


def _configurable_option_dests(parser: argparse.ArgumentParser) -> list[str]:
    excluded = {"help", "ean", "load_config", "save_config", "config_dir"}
    dests: list[str] = []
    for action in parser._actions:
        if not action.option_strings:
            continue
        if action.dest in excluded:
            continue
        dests.append(action.dest)
    return dests


def _provided_option_dests(parser: argparse.ArgumentParser, argv: list[str]) -> set[str]:
    option_to_dest: dict[str, str] = {}
    for action in parser._actions:
        for opt in action.option_strings:
            option_to_dest[opt] = action.dest

    provided: set[str] = set()
    for token in argv:
        if not token.startswith("--"):
            continue
        option = token.split("=", 1)[0]
        dest = option_to_dest.get(option)
        if dest:
            provided.add(dest)
    return provided


def _resolve_config_path(config_name: str, config_dir: str) -> Path:
    path = Path(config_name)
    if not path.suffix:
        path = path.with_suffix(".json")
    if not path.is_absolute():
        path = Path(config_dir) / path
    return path


def _load_config_file(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Config-Datei ist kein JSON-Objekt.")
    if "settings" in raw:
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("Config-Feld 'settings' muss ein JSON-Objekt sein.")
        return settings
    return raw


def _save_config_file(path: Path, settings: dict) -> None:
    payload = {
        "version": 1,
        "settings": settings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def get_default_settings() -> dict:
    parser = build_parser()
    keys = _configurable_option_dests(parser)
    parsed_defaults = parser.parse_args(["400638133393"])
    return {key: getattr(parsed_defaults, key) for key in keys}


def build_settings_namespace(settings: dict) -> argparse.Namespace:
    defaults = get_default_settings()
    merged = defaults.copy()
    for key, value in settings.items():
        if key in defaults:
            merged[key] = value
    return argparse.Namespace(**merged)


def list_config_names(config_dir: str = DEFAULT_CONFIG_DIR) -> list[str]:
    base_dir = Path(config_dir)
    if not base_dir.exists():
        return []
    names: list[str] = []
    for path in base_dir.glob("*.json"):
        if path.is_file():
            names.append(path.stem)
    return sorted(names)


def load_config_settings(config_name: str, config_dir: str = DEFAULT_CONFIG_DIR) -> dict:
    path = _resolve_config_path(config_name, config_dir)
    if not path.exists():
        raise FileNotFoundError(f"Config nicht gefunden: {path}")
    loaded = _load_config_file(path)
    defaults = get_default_settings()
    merged = defaults.copy()
    for key, value in loaded.items():
        if key in defaults:
            merged[key] = value
    return merged


def save_config_settings(config_name: str, settings: dict, config_dir: str = DEFAULT_CONFIG_DIR) -> Path:
    path = _resolve_config_path(config_name, config_dir)
    defaults = get_default_settings()
    effective = defaults.copy()
    for key, value in settings.items():
        if key in defaults:
            effective[key] = value
    _save_config_file(path, effective)
    return path


def _parse_svg_color(value: str | None) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"none", "transparent"}:
        return None
    try:
        rgb = ImageColor.getrgb(cleaned)
    except Exception:
        return None
    if len(rgb) == 4:
        return rgb
    return (rgb[0], rgb[1], rgb[2], 255)


def _extract_fill_color(element: ET.Element, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    style = _parse_style(element.attrib.get("style", ""))
    if "fill" in style:
        style_color = _parse_svg_color(style.get("fill"))
        if style_color is None:
            return None
        return style_color
    if "fill" in element.attrib:
        attr_color = _parse_svg_color(element.attrib.get("fill"))
        if attr_color is None:
            return None
        return attr_color
    return default


def _load_pillow_font_from_family(font_family: str, size_px: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    discovered = _discover_system_font_file(font_family)
    if discovered is not None:
        try:
            return ImageFont.truetype(str(discovered), size=max(1, int(round(size_px))))
        except Exception:
            pass
    for fallback in ("DejaVuSans.ttf", "arial.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(fallback, size=max(1, int(round(size_px))))
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_spaced_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    start_x: float,
    y: float,
    target_width: float | None,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
) -> None:
    if not text:
        return
    advances: list[float] = []
    for ch in text:
        bbox = draw.textbbox((0, 0), ch, font=font, anchor="ls")
        advances.append(float(bbox[2] - bbox[0]))
    natural = sum(advances)
    gap = 0.0
    if target_width is not None and len(text) > 1:
        gap = (target_width - natural) / (len(text) - 1)
    cursor = start_x
    for i, ch in enumerate(text):
        draw.text((cursor, y), ch, font=font, fill=fill, anchor="ls")
        cursor += advances[i]
        if i < len(text) - 1:
            cursor += gap


def render_png_bytes(svg_content: str, width_px: int, height_px: int | None) -> bytes:
    root = ET.fromstring(svg_content)
    view_box = root.attrib.get("viewBox")
    if not view_box:
        raise ValueError("PNG-Export erwartet ein SVG mit viewBox.")
    parts = view_box.replace(",", " ").split()
    if len(parts) != 4:
        raise ValueError("Ungültige viewBox im SVG.")

    vb_x, vb_y, vb_w, vb_h = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    if vb_w <= 0 or vb_h <= 0:
        raise ValueError("Ungültige viewBox-Dimensionen für PNG-Export.")

    out_w = int(width_px)
    out_h = int(height_px) if height_px is not None else max(1, int(round(out_w * (vb_h / vb_w))))
    sx = out_w / vb_w
    sy = out_h / vb_h

    image = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    default_fill = (0, 0, 0, 255)

    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        width_attr = element.attrib.get("width", "")
        height_attr = element.attrib.get("height", "")
        if width_attr.endswith("%") or height_attr.endswith("%"):
            if width_attr == "100%" and height_attr == "100%":
                fill = _extract_fill_color(element, default_fill)
                if fill is not None:
                    draw.rectangle([(0, 0), (out_w, out_h)], fill=fill)
            continue

        try:
            x = float(element.attrib.get("x", "0"))
            y = float(element.attrib.get("y", "0"))
            w = float(width_attr)
            h = float(height_attr)
        except ValueError:
            continue

        fill = _extract_fill_color(element, default_fill)
        if fill is None:
            continue
        x0 = (x - vb_x) * sx
        y0 = (y - vb_y) * sy
        x1 = (x + w - vb_x) * sx
        y1 = (y + h - vb_y) * sy
        draw.rectangle([(x0, y0), (x1, y1)], fill=fill)

    for element in root.iter():
        if not element.tag.endswith("text"):
            continue
        style = _parse_style(element.attrib.get("style", ""))
        fill = _parse_svg_color(style.get("fill")) or default_fill
        anchor = style.get("text-anchor", "start")
        font_size_px = float(style.get("font-size", "10").replace("px", "") or "10")
        render_font_size_px = max(1.0, font_size_px * sy)
        font_family = style.get("font-family", "")
        font = _load_pillow_font_from_family(font_family, render_font_size_px)

        try:
            x = float(element.attrib.get("x", "0"))
            y = float(element.attrib.get("y", "0"))
        except ValueError:
            continue

        text = element.text or ""
        if not text:
            continue
        text_length = None
        if "textLength" in element.attrib:
            try:
                text_length = float(element.attrib["textLength"])
            except ValueError:
                text_length = None

        x_px = (x - vb_x) * sx
        y_px = (y - vb_y) * sy
        if text_length is not None:
            target_width_px = text_length * sx
            if anchor == "middle":
                start_x = x_px - (target_width_px / 2.0)
            elif anchor == "end":
                start_x = x_px - target_width_px
            else:
                start_x = x_px
            _draw_spaced_text(draw, text, start_x, y_px, target_width_px, font, fill)
            continue

        if anchor == "middle":
            draw.text((x_px, y_px), text, font=font, fill=fill, anchor="ms")
        elif anchor == "end":
            draw.text((x_px, y_px), text, font=font, fill=fill, anchor="rs")
        else:
            draw.text((x_px, y_px), text, font=font, fill=fill, anchor="ls")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_png_from_svg(svg_content: str, output_path: Path, width_px: int, height_px: int | None) -> None:
    output_path.write_bytes(render_png_bytes(svg_content, width_px, height_px))


def _scale_bars_for_aspect_ratio(
    root: ET.Element, target_ratio: float
) -> tuple[float, float, float, float] | None:
    bars: list[tuple[ET.Element, float, float, float, float]] = []
    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        if element.attrib.get("width") == "100%" and element.attrib.get("height") == "100%":
            continue
        try:
            x = _to_mm(element.attrib["x"])
            y = _to_mm(element.attrib["y"])
            width = _to_mm(element.attrib["width"])
            height = _to_mm(element.attrib["height"])
        except (KeyError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        bars.append((element, x, y, width, height))

    if not bars:
        return None

    x_min = min(item[1] for item in bars)
    y_min = min(item[2] for item in bars)
    x_max = max(item[1] + item[3] for item in bars)
    y_max = max(item[2] + item[4] for item in bars)
    current_ratio = (x_max - x_min) / (y_max - y_min)
    if current_ratio <= 0:
        return None

    factor = target_ratio / current_ratio
    if abs(factor - 1.0) < 1e-4:
        return None

    sx = factor ** 0.5
    sy = 1.0 / sx

    for element, x, y, width, height in bars:
        new_x = x_min + ((x - x_min) * sx)
        new_y = y_min + ((y - y_min) * sy)
        new_width = width * sx
        new_height = height * sy
        element.set("x", f"{new_x:.3f}mm")
        element.set("y", f"{new_y:.3f}mm")
        element.set("width", f"{new_width:.3f}mm")
        element.set("height", f"{new_height:.3f}mm")
    return sx, sy, x_min, y_min


def _shift_bars(root: ET.Element, dx_mm: float, dy_mm: float = 0.0) -> None:
    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        if element.attrib.get("width") == "100%" and element.attrib.get("height") == "100%":
            continue
        try:
            x = _to_mm(element.attrib["x"])
            y = _to_mm(element.attrib["y"])
        except (KeyError, ValueError):
            continue
        element.set("x", f"{x + dx_mm:.3f}mm")
        element.set("y", f"{y + dy_mm:.3f}mm")


def _fit_and_center_bars_horizontally(root: ET.Element, width_px: int) -> int:
    module_width, x_min, x_max, _y_bottom = _find_bar_metrics_mm(root)
    bars_width_mm = x_max - x_min
    mm_per_px = UNIT_TO_MM["px"]
    viewport_width_mm = width_px * mm_per_px
    side_margin_mm = max(1.0, module_width * 2.0)
    required_width_mm = bars_width_mm + (2.0 * side_margin_mm)

    if required_width_mm > viewport_width_mm:
        width_px = max(width_px, int((required_width_mm / mm_per_px) + 0.999))
        viewport_width_mm = width_px * mm_per_px

    target_x_min = (viewport_width_mm - bars_width_mm) / 2.0
    dx_mm = target_x_min - x_min
    _shift_bars(root, dx_mm=dx_mm, dy_mm=0.0)
    return width_px


def _apply_text_layout(
    root: ET.Element,
    ean13: str,
    args: argparse.Namespace,
    text_color: str,
) -> None:
    _remove_text_nodes(root)
    if args.no_text:
        return

    _module_width, x_min, x_max, y_bottom = _find_bar_metrics_mm(root)
    module_width = (x_max - x_min) / 95.0
    font_size_mm = args.font_size * UNIT_TO_MM["px"]
    gap_mm = args.text_y_offset * module_width
    text_y = y_bottom + gap_mm + (TEXT_ASCENT_FACTOR * font_size_mm)
    group = _find_barcode_group(root)

    text_origin = x_min + (args.leading_digit_offset * module_width)

    # Simples Layout: ein Text, gleichmäßig über die volle Barcodebreite verteilt.
    _add_text_node(
        group,
        ean13,
        text_origin + (47.5 * module_width),
        text_y,
        text_color,
        args.font_size,
        args.font_family,
        "middle",
        text_length_mm=(95.0 * module_width),
    )


def _resolve_render_background(foreground: str, background: str) -> str:
    if background != "transparent":
        return background
    if foreground.lower() == "#ffffff":
        return "#fffffe"
    return "#ffffff"


def generate_svg(args: argparse.Namespace, normalized_ean: str, apply_text_to_path: bool = True) -> str:
    foreground = validate_color(args.foreground, allow_transparent=False)
    background = validate_color(args.background, allow_transparent=True)
    text_color = (
        validate_color(args.text_color, allow_transparent=False)
        if args.text_color
        else foreground
    )
    if background != "transparent" and foreground == background:
        raise ValueError("Foreground und Background sind identisch; Barcode wäre unsichtbar.")

    if args.width_px is not None and args.width_px <= 0:
        raise ValueError("--width-px muss größer als 0 sein.")
    if args.height_px is not None and args.height_px <= 0:
        raise ValueError("--height-px muss größer als 0 sein.")
    if args.aspect_ratio is not None and args.aspect_ratio <= 0:
        raise ValueError("--aspect-ratio muss größer als 0 sein.")
    if args.font_size <= 0:
        raise ValueError("--font-size muss größer als 0 sein.")
    if not args.font_family.strip():
        raise ValueError("--font-family darf nicht leer sein.")
    embedded_font_file: Path | None = None
    text_to_path_font_file: Path | None = None
    effective_font_family = args.font_family
    if args.embed_font_file:
        embedded_font_file = Path(args.embed_font_file)
        if not embedded_font_file.exists():
            raise ValueError("--embed-font-file wurde nicht gefunden.")
        effective_font_family = "EANEmbeddedFont"
    if args.text_to_path and apply_text_to_path:
        raw_path = args.text_to_path_font_file or args.embed_font_file
        if raw_path:
            text_to_path_font_file = Path(raw_path)
            if not text_to_path_font_file.exists():
                raise ValueError("Datei für --text-to-path wurde nicht gefunden.")
        else:
            text_to_path_font_file = _discover_system_font_file(args.font_family)
            if text_to_path_font_file is None:
                raise ValueError(
                    "Keine passende Systemschrift für --text-to-path gefunden. "
                    "Nutze --text-to-path-font-file oder --embed-font-file."
                )

    data12 = normalized_ean[:12]
    code = EAN13(data12, writer=SVGWriter())
    writer_options = {
        "write_text": True,
        "foreground": foreground,
        "background": _resolve_render_background(foreground, background),
    }

    svg_bytes = code.render(writer_options=writer_options)
    svg_text = svg_bytes.decode("utf-8")

    root = ET.fromstring(svg_text)
    if args.aspect_ratio is not None:
        _scale_bars_for_aspect_ratio(root, args.aspect_ratio)

    if embedded_font_file is not None and (not args.text_to_path or not apply_text_to_path):
        _inject_embedded_font(root, embedded_font_file, effective_font_family)

    original_font_family = args.font_family
    args.font_family = effective_font_family
    _apply_text_layout(root, normalized_ean, args, text_color)
    args.font_family = original_font_family

    if background == "transparent":
        make_background_transparent(root)

    _normalize_units_to_px(root)
    _set_tight_viewbox_and_canvas(root, args.width_px, args.height_px)
    if args.text_to_path and apply_text_to_path and text_to_path_font_file is not None:
        _replace_text_with_paths(root, text_to_path_font_file)
    if "preserveAspectRatio" in root.attrib:
        del root.attrib["preserveAspectRatio"]

    return ET.tostring(root, encoding="unicode")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config_keys = set(_configurable_option_dests(parser))
    provided_keys = _provided_option_dests(parser, sys.argv[1:])

    if args.load_config:
        config_path = _resolve_config_path(args.load_config, args.config_dir)
        if not config_path.exists():
            print(f"Fehler: Config nicht gefunden: {config_path}", file=sys.stderr)
            return 2
        try:
            loaded_settings = _load_config_file(config_path)
        except Exception as error:
            print(f"Fehler: Config konnte nicht geladen werden: {error}", file=sys.stderr)
            return 2

        for key, value in loaded_settings.items():
            if key not in config_keys:
                continue
            if key in provided_keys:
                continue
            setattr(args, key, value)

    try:
        normalized_ean = normalize_ean(args.ean)
        svg_content = generate_svg(
            args,
            normalized_ean,
            apply_text_to_path=(args.output_format == "svg"),
        )
    except ValueError as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Unerwarteter Fehler: {error}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    extension = "png" if args.output_format == "png" else "svg"
    output_path = output_dir / f"barcode_{normalized_ean}.{extension}"

    try:
        if args.save_config:
            config_path = _resolve_config_path(args.save_config, args.config_dir)
            effective_settings = {key: getattr(args, key) for key in sorted(config_keys)}
            _save_config_file(config_path, effective_settings)
        output_dir.mkdir(parents=True, exist_ok=True)
        if args.output_format == "png":
            png_width = args.width_px if args.width_px is not None else 1000
            png_height = args.height_px
            _write_png_from_svg(svg_content, output_path, png_width, png_height)
        else:
            output_path.write_text(svg_content, encoding="utf-8")
    except Exception as error:
        print(f"Unerwarteter Fehler: {error}", file=sys.stderr)
        return 1

    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
