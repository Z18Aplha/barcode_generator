#!/usr/bin/env python3
import io
import re
import sys
import zipfile
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from barcode_generator_core import (  # noqa: E402
    DEFAULT_CONFIG_DIR,
    build_settings_namespace,
    generate_svg,
    get_default_settings,
    list_config_names,
    load_config_settings,
    normalize_ean,
    render_png_bytes,
    save_config_settings,
)


def _safe_hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    cleaned = value.strip()
    if not cleaned:
        return fallback
    if not cleaned.startswith("#"):
        cleaned = f"#{cleaned}"
    if len(cleaned) == 4:
        return "#" + "".join(ch * 2 for ch in cleaned[1:])
    if len(cleaned) == 7:
        return cleaned
    return fallback


def _get_query_preset() -> str:
    try:
        value = st.query_params.get("preset", "")
    except Exception:
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _set_query_preset(preset_name: str) -> None:
    try:
        if preset_name:
            st.query_params["preset"] = preset_name
        elif "preset" in st.query_params:
            del st.query_params["preset"]
    except Exception:
        pass


def _init_state() -> None:
    defaults = get_default_settings()
    if "ean_input" not in st.session_state:
        st.session_state["ean_input"] = "400638133393"

    if "ui_output_format" not in st.session_state:
        st.session_state["ui_output_format"] = defaults["output_format"]
    if "ui_foreground_picker" not in st.session_state:
        st.session_state["ui_foreground_picker"] = _safe_hex(defaults["foreground"], "#000000")
    if "ui_foreground_override" not in st.session_state:
        st.session_state["ui_foreground_override"] = ""

    background = defaults["background"]
    if "ui_background_mode" not in st.session_state:
        st.session_state["ui_background_mode"] = (
            "Transparent" if background in {"transparent", "none"} else "Farbe"
        )
    if "ui_background_picker" not in st.session_state:
        st.session_state["ui_background_picker"] = _safe_hex(background, "#ffffff")
    if "ui_background_override" not in st.session_state:
        st.session_state["ui_background_override"] = ""

    if "ui_use_width_px" not in st.session_state:
        st.session_state["ui_use_width_px"] = defaults["width_px"] is not None
    if "ui_width_px" not in st.session_state:
        st.session_state["ui_width_px"] = defaults["width_px"] or 450
    if "ui_use_height_px" not in st.session_state:
        st.session_state["ui_use_height_px"] = defaults["height_px"] is not None
    if "ui_height_px" not in st.session_state:
        st.session_state["ui_height_px"] = defaults["height_px"] or 220
    if "ui_use_aspect_ratio" not in st.session_state:
        st.session_state["ui_use_aspect_ratio"] = defaults["aspect_ratio"] is not None
    if "ui_aspect_ratio" not in st.session_state:
        st.session_state["ui_aspect_ratio"] = defaults["aspect_ratio"] or 2.0

    if "ui_no_text" not in st.session_state:
        st.session_state["ui_no_text"] = defaults["no_text"]
    if "ui_font_size" not in st.session_state:
        st.session_state["ui_font_size"] = float(defaults["font_size"])
    if "ui_text_y_offset" not in st.session_state:
        st.session_state["ui_text_y_offset"] = float(defaults["text_y_offset"])

    if "ui_text_color_enabled" not in st.session_state:
        st.session_state["ui_text_color_enabled"] = defaults["text_color"] is not None
    if "ui_text_color_picker" not in st.session_state:
        st.session_state["ui_text_color_picker"] = _safe_hex(defaults["text_color"], "#000000")
    if "ui_text_color_override" not in st.session_state:
        st.session_state["ui_text_color_override"] = ""
    if "ui_font_family" not in st.session_state:
        st.session_state["ui_font_family"] = defaults["font_family"]
    if "ui_leading_digit_offset" not in st.session_state:
        st.session_state["ui_leading_digit_offset"] = float(defaults["leading_digit_offset"])
    if "ui_text_layout" not in st.session_state:
        st.session_state["ui_text_layout"] = defaults["text_layout"]
    if "ui_text_to_path" not in st.session_state:
        st.session_state["ui_text_to_path"] = defaults["text_to_path"]
    if "ui_embed_font_file" not in st.session_state:
        st.session_state["ui_embed_font_file"] = defaults["embed_font_file"] or ""
    if "ui_text_to_path_font_file" not in st.session_state:
        st.session_state["ui_text_to_path_font_file"] = defaults["text_to_path_font_file"] or ""
    if "ui_output_dir" not in st.session_state:
        st.session_state["ui_output_dir"] = defaults["output_dir"]

    if "ui_config_dir" not in st.session_state:
        st.session_state["ui_config_dir"] = DEFAULT_CONFIG_DIR
    if "ui_save_config_name" not in st.session_state:
        st.session_state["ui_save_config_name"] = ""
    if "ui_selected_preset" not in st.session_state:
        st.session_state["ui_selected_preset"] = ""
    if "ui_last_loaded_preset" not in st.session_state:
        st.session_state["ui_last_loaded_preset"] = ""
    if "ui_batch_input" not in st.session_state:
        st.session_state["ui_batch_input"] = ""


def _apply_settings_to_state(settings: dict) -> None:
    st.session_state["ui_output_format"] = settings.get("output_format", "svg")
    st.session_state["ui_foreground_picker"] = _safe_hex(settings.get("foreground"), "#000000")
    st.session_state["ui_foreground_override"] = ""

    background = settings.get("background", "#ffffff")
    st.session_state["ui_background_mode"] = (
        "Transparent" if background in {"transparent", "none"} else "Farbe"
    )
    st.session_state["ui_background_picker"] = _safe_hex(background, "#ffffff")
    st.session_state["ui_background_override"] = ""

    width_px = settings.get("width_px")
    height_px = settings.get("height_px")
    aspect_ratio = settings.get("aspect_ratio")
    st.session_state["ui_use_width_px"] = width_px is not None
    st.session_state["ui_width_px"] = int(width_px) if width_px is not None else 450
    st.session_state["ui_use_height_px"] = height_px is not None
    st.session_state["ui_height_px"] = int(height_px) if height_px is not None else 220
    st.session_state["ui_use_aspect_ratio"] = aspect_ratio is not None
    st.session_state["ui_aspect_ratio"] = float(aspect_ratio) if aspect_ratio is not None else 2.0

    st.session_state["ui_no_text"] = bool(settings.get("no_text", False))
    st.session_state["ui_font_size"] = float(settings.get("font_size", 10.0))
    st.session_state["ui_text_y_offset"] = float(settings.get("text_y_offset", 1.0))
    text_color = settings.get("text_color")
    st.session_state["ui_text_color_enabled"] = text_color is not None
    st.session_state["ui_text_color_picker"] = _safe_hex(text_color, "#000000")
    st.session_state["ui_text_color_override"] = ""
    st.session_state["ui_font_family"] = settings.get("font_family", "OCR-B, OCRB, monospace")
    st.session_state["ui_leading_digit_offset"] = float(settings.get("leading_digit_offset", 0.0))
    st.session_state["ui_text_layout"] = settings.get("text_layout", "ean-grouped")
    st.session_state["ui_text_to_path"] = bool(settings.get("text_to_path", False))
    st.session_state["ui_embed_font_file"] = settings.get("embed_font_file") or ""
    st.session_state["ui_text_to_path_font_file"] = settings.get("text_to_path_font_file") or ""
    st.session_state["ui_output_dir"] = settings.get("output_dir", ".")


def _collect_settings() -> dict:
    foreground = st.session_state["ui_foreground_override"].strip() or st.session_state["ui_foreground_picker"]
    if st.session_state["ui_background_mode"] == "Transparent":
        background = "transparent"
    else:
        background = (
            st.session_state["ui_background_override"].strip()
            or st.session_state["ui_background_picker"]
        )

    text_color = None
    if st.session_state["ui_text_color_enabled"]:
        text_color = (
            st.session_state["ui_text_color_override"].strip()
            or st.session_state["ui_text_color_picker"]
        )

    settings = {
        "output_format": st.session_state["ui_output_format"],
        "foreground": foreground,
        "background": background,
        "width_px": int(st.session_state["ui_width_px"]) if st.session_state["ui_use_width_px"] else None,
        "height_px": int(st.session_state["ui_height_px"]) if st.session_state["ui_use_height_px"] else None,
        "aspect_ratio": float(st.session_state["ui_aspect_ratio"])
        if st.session_state["ui_use_aspect_ratio"]
        else None,
        "no_text": bool(st.session_state["ui_no_text"]),
        "text_layout": st.session_state["ui_text_layout"],
        "font_family": st.session_state["ui_font_family"],
        "font_size": float(st.session_state["ui_font_size"]),
        "text_color": text_color,
        "leading_digit_offset": float(st.session_state["ui_leading_digit_offset"]),
        "text_y_offset": float(st.session_state["ui_text_y_offset"]),
        "embed_font_file": st.session_state["ui_embed_font_file"].strip() or None,
        "text_to_path": bool(st.session_state["ui_text_to_path"]),
        "text_to_path_font_file": st.session_state["ui_text_to_path_font_file"].strip() or None,
        "output_dir": st.session_state["ui_output_dir"].strip() or ".",
    }
    return settings


def _parse_batch_eans(raw_input: str) -> list[str]:
    parts = re.split(r"[\n,]+", raw_input)
    return [item.strip() for item in parts if item.strip()]


def _build_batch_zip(settings: dict, ean_list: list[str]) -> tuple[bytes, list[str], list[str]]:
    errors: list[str] = []
    normalized: list[str] = []
    output_format = settings.get("output_format", "svg")
    extension = "png" if output_format == "png" else "svg"
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        filename_counts: dict[str, int] = {}
        for raw_ean in ean_list:
            try:
                normalized_ean = normalize_ean(raw_ean)
                args = build_settings_namespace(settings)
                svg_content = generate_svg(
                    args,
                    normalized_ean,
                    apply_text_to_path=(output_format == "svg"),
                )
                if output_format == "svg":
                    payload = svg_content.encode("utf-8")
                else:
                    png_width = settings["width_px"] if settings["width_px"] is not None else 1000
                    payload = render_png_bytes(svg_content, png_width, settings["height_px"])
            except Exception as error:
                errors.append(f"{raw_ean}: {error}")
                continue

            base_name = f"barcode_{normalized_ean}.{extension}"
            count = filename_counts.get(base_name, 0)
            filename_counts[base_name] = count + 1
            if count > 0:
                output_name = f"barcode_{normalized_ean}_{count + 1}.{extension}"
            else:
                output_name = base_name
            archive.writestr(output_name, payload)
            normalized.append(normalized_ean)

    return zip_buffer.getvalue(), normalized, errors


def main() -> None:
    st.set_page_config(page_title="EAN-13 Barcode WebApp", layout="wide")
    _init_state()

    # Preset aus URL übernehmen (z. B. bei Seiten-Reload).
    query_preset = _get_query_preset().strip()
    if query_preset and st.session_state["ui_selected_preset"] != query_preset:
        st.session_state["ui_selected_preset"] = query_preset

    st.title("EAN-13 Barcode Generator")
    st.caption("Lokale WebApp mit Live-Vorschau und Download als SVG oder PNG.")

    with st.sidebar:
        st.subheader("Preset / Config")
        st.text_input("Config Dir", key="ui_config_dir")
        preset_names = list_config_names(st.session_state["ui_config_dir"])
        preset_options = [""] + preset_names
        if st.session_state["ui_selected_preset"] not in preset_options:
            st.session_state["ui_selected_preset"] = ""
        selected_preset = st.selectbox(
            "Preset wählen",
            options=preset_options,
            key="ui_selected_preset",
            format_func=lambda v: "(keins)" if v == "" else v,
        )

        loaded_preview: dict | None = None
        if selected_preset:
            try:
                loaded_preview = load_config_settings(
                    selected_preset,
                    st.session_state["ui_config_dir"],
                )
            except Exception as error:
                st.error(f"Config konnte nicht geladen werden: {error}")
            else:
                st.caption("Preset-Inhalt")
                st.info(
                    f"Format im Preset: {str(loaded_preview.get('output_format', 'svg')).upper()}"
                )
                st.json(loaded_preview, expanded=False)

                # Preset bei Auswahl automatisch anwenden (einmal pro Preset-Wechsel).
                if st.session_state["ui_last_loaded_preset"] != selected_preset:
                    _apply_settings_to_state(loaded_preview)
                    st.session_state["ui_last_loaded_preset"] = selected_preset
                    _set_query_preset(selected_preset)
                    st.rerun()
        else:
            if st.session_state["ui_last_loaded_preset"]:
                st.session_state["ui_last_loaded_preset"] = ""
            _set_query_preset("")

        st.text_input("Save as", key="ui_save_config_name")
        if st.button("Save Preset", use_container_width=True):
            name = st.session_state["ui_save_config_name"].strip()
            if not name:
                st.warning("Bitte einen Namen für das Config-Set angeben.")
            else:
                try:
                    save_config_settings(
                        config_name=name,
                        settings=_collect_settings(),
                        config_dir=st.session_state["ui_config_dir"],
                    )
                    st.success(f"Config gespeichert: {name}")
                except Exception as error:
                    st.error(f"Config konnte nicht gespeichert werden: {error}")

        st.divider()
        st.subheader("Konfiguration")
        st.text_input("EAN (12 oder 13 Ziffern)", key="ean_input")
        st.selectbox("Ausgabeformat", ["svg", "png"], key="ui_output_format")

        st.markdown("**Farben**")
        st.color_picker("Foreground", key="ui_foreground_picker")
        st.text_input("Foreground Override (optional)", key="ui_foreground_override")
        st.radio("Hintergrund", ["Farbe", "Transparent"], key="ui_background_mode", horizontal=True)
        if st.session_state["ui_background_mode"] == "Farbe":
            st.color_picker("Background", key="ui_background_picker")
            st.text_input("Background Override (optional)", key="ui_background_override")

        st.markdown("**Größe**")
        st.checkbox("Width aktiv", key="ui_use_width_px")
        if st.session_state["ui_use_width_px"]:
            st.number_input("Width px", min_value=1, step=1, key="ui_width_px")
        st.checkbox("Height aktiv", key="ui_use_height_px")
        if st.session_state["ui_use_height_px"]:
            st.number_input("Height px", min_value=1, step=1, key="ui_height_px")
        st.checkbox("Aspect Ratio aktiv", key="ui_use_aspect_ratio")
        if st.session_state["ui_use_aspect_ratio"]:
            st.slider("Aspect Ratio", min_value=0.2, max_value=8.0, step=0.05, key="ui_aspect_ratio")

        st.markdown("**Text**")
        st.checkbox("Text ausblenden (--no-text)", key="ui_no_text")
        st.slider("Font Size", min_value=6.0, max_value=64.0, step=0.5, key="ui_font_size")
        st.slider("Text Y Offset", min_value=0.0, max_value=8.0, step=0.1, key="ui_text_y_offset")

        with st.expander("Erweitert", expanded=False):
            st.checkbox("Eigene Textfarbe", key="ui_text_color_enabled")
            if st.session_state["ui_text_color_enabled"]:
                st.color_picker("Textfarbe", key="ui_text_color_picker")
                st.text_input("Textfarbe Override (optional)", key="ui_text_color_override")
            st.text_input("Font Family", key="ui_font_family")
            st.number_input("Leading Digit Offset", step=0.1, key="ui_leading_digit_offset")
            st.selectbox("Text Layout", ["ean-grouped", "single"], key="ui_text_layout")
            st.checkbox("Text zu Pfad (nur SVG)", key="ui_text_to_path")
            st.text_input("Embed Font File (Container-Pfad)", key="ui_embed_font_file")
            st.text_input("Text-to-Path Font File (Container-Pfad)", key="ui_text_to_path_font_file")
            st.text_input("Output Dir (nur für Config)", key="ui_output_dir")

    settings = _collect_settings()
    ean_input = st.session_state["ean_input"].strip()

    try:
        normalized_ean = normalize_ean(ean_input)
        args = build_settings_namespace(settings)
        svg_content = generate_svg(
            args,
            normalized_ean,
            apply_text_to_path=(settings["output_format"] == "svg"),
        )
        svg_bytes = svg_content.encode("utf-8")
    except Exception as error:
        st.error(f"{error}")
        st.stop()

    st.subheader("Vorschau")
    if settings["output_format"] == "svg":
        components.html(svg_content, height=320, scrolling=False)
        st.download_button(
            "SVG herunterladen",
            data=svg_bytes,
            file_name=f"barcode_{normalized_ean}.svg",
            mime="image/svg+xml",
            use_container_width=True,
        )
    else:
        png_width = settings["width_px"] if settings["width_px"] is not None else 1000
        try:
            png_bytes = render_png_bytes(svg_content, png_width, settings["height_px"])
        except Exception as error:
            st.error(f"PNG konnte nicht erzeugt werden: {error}")
            st.stop()
        st.image(png_bytes, use_container_width=False)
        st.download_button(
            "PNG herunterladen",
            data=png_bytes,
            file_name=f"barcode_{normalized_ean}.png",
            mime="image/png",
            use_container_width=True,
        )

    st.divider()
    st.subheader("Batch")
    st.caption("Mehrere EANs als ZIP generieren (komma- oder zeilengetrennt).")
    st.text_area(
        "EAN-Liste",
        key="ui_batch_input",
        placeholder="400638133393\n4262479210243, 4006381333931",
        height=140,
    )
    batch_values = _parse_batch_eans(st.session_state["ui_batch_input"])
    batch_zip_bytes = b""
    batch_ok = False
    if batch_values:
        batch_zip_bytes, batch_normalized, batch_errors = _build_batch_zip(settings, batch_values)
        if batch_errors:
            st.error("Batch-Fehler:\n- " + "\n- ".join(batch_errors))
        elif not batch_normalized:
            st.warning("Keine gültigen EANs im Batch gefunden.")
        else:
            st.success(f"{len(batch_normalized)} Code(s) bereit für ZIP-Download.")
            batch_ok = True
    else:
        st.info("EAN-Liste eingeben, um Batch Download zu aktivieren.")

    zip_name_suffix = "png" if settings["output_format"] == "png" else "svg"
    st.download_button(
        "Batch Download",
        data=batch_zip_bytes if batch_ok else b"",
        file_name=f"barcodes_{zip_name_suffix}.zip",
        mime="application/zip",
        disabled=not batch_ok,
        use_container_width=True,
    )

    st.caption(f"Normalisierte EAN-13: {normalized_ean}")


if __name__ == "__main__":
    main()
