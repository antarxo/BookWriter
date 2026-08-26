from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from docx.shared import Pt, RGBColor



def _font_name(typography: dict[str, Any]) -> str | None:
    value = ((typography.get("fontFamily") or {}).get("dominant"))
    return str(value).strip() if value else None


def _font_size(typography: dict[str, Any]) -> float | None:
    value = ((typography.get("fontSizePt") or {}).get("dominant"))
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pdf_color(value: Any) -> RGBColor | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    number &= 0xFFFFFF
    return RGBColor((number >> 16) & 0xFF, (number >> 8) & 0xFF, number & 0xFF)


def _contract_run(text: str, item: dict[str, Any]) -> dict[str, Any]:
    typography = item.get("__pdfTypography") if isinstance(item.get("__pdfTypography"), dict) else {}
    emphasis = typography.get("emphasis") if isinstance(typography.get("emphasis"), dict) else {}
    bold_ratio = emphasis.get("boldRatio")
    italic_ratio = emphasis.get("italicRatio")
    superscript_ratio = emphasis.get("superscriptRatio")
    underline_ratio = emphasis.get("underlineRatio")
    color = ((typography.get("color") or {}).get("dominant"))
    return {
        "text": text,
        "bold": bool(isinstance(bold_ratio, (int, float)) and float(bold_ratio) >= 0.55),
        "italic": bool(isinstance(italic_ratio, (int, float)) and float(italic_ratio) >= 0.55),
        "underline": bool(isinstance(underline_ratio, (int, float)) and float(underline_ratio) >= 0.55),
        "superscript": bool(isinstance(superscript_ratio, (int, float)) and float(superscript_ratio) >= 0.55),
        "font": _font_name(typography),
        "size_pt": _font_size(typography),
        "pdf_color": color,
        "__contract": True,
    }


def _region_contracts(contract: dict[str, Any], page_structure: dict[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    by_slot: dict[tuple[int, str], dict[str, Any]] = {}
    for item in contract.get("items", []) or []:
        placement = item.get("placement") or {}
        page = int(placement.get("page") or 0)
        slot = str(placement.get("slotId") or "")
        if page and slot:
            by_slot[(page, slot)] = item
    result: dict[tuple[str, ...], dict[str, Any]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for item in page.get("flow", []) or []:
            slot = str(item.get("id") or "")
            contract_item = by_slot.get((page_no, slot))
            if contract_item is None and slot.startswith("flow-"):
                contract_item = by_slot.get((page_no, slot[5:]))
            if contract_item is None:
                continue
            region_ids = tuple(str(value) for value in item.get("region_ids", []) or [])
            if region_ids:
                result[region_ids] = contract_item
    return result


def _apply_contract_to_structure(contract: dict[str, Any], page_structure: dict[str, Any]) -> None:
    by_slot: dict[tuple[int, str], dict[str, Any]] = {}
    for contract_item in contract.get("items", []) or []:
        placement = contract_item.get("placement") or {}
        page = int(placement.get("page") or 0)
        slot = str(placement.get("slotId") or "")
        if page and slot:
            by_slot[(page, slot)] = contract_item
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for item in page.get("flow", []) or []:
            slot = str(item.get("id") or "")
            contract_item = by_slot.get((page_no, slot))
            if contract_item is None and slot.startswith("flow-"):
                contract_item = by_slot.get((page_no, slot[5:]))
            if contract_item is None:
                continue
            item["__wordParagraph"] = contract_item.get("wordParagraph") or {}
            item["__pdfTypography"] = contract_item.get("pdfTypography") or {}
            item["__buildContractId"] = contract_item.get("id")


@contextmanager
def contract_typography_bridge(legacy_module: Any, contract: dict[str, Any], page_structure: dict[str, Any]) -> Iterator[None]:
    """Temporarily route legacy text helpers through the maps-first contract.

    This bridge is deliberately narrow: it changes only ordinary flow-text
    typography/line-height/gap decisions. Paragraph alignment/indents and callout
    frame formatting remain explicit follow-up work rather than hidden guesses.
    """
    _apply_contract_to_structure(contract, page_structure)
    region_map = _region_contracts(contract, page_structure)

    original_item_text = legacy_module._item_text_and_runs
    original_add_runs = legacy_module._add_runs
    original_dominant = legacy_module._dominant_span_size
    original_max = legacy_module._max_span_size
    original_line_height = legacy_module._line_height
    original_gap = legacy_module._word_flow_gap

    def item_text_and_runs(item: dict[str, Any], matches: dict[str, Any], docx_paras: dict[str, Any]):
        text = str(item.get("text") or "")
        if item.get("type") == "text" and item.get("__pdfTypography"):
            return text, [_contract_run(text, item)], "markdown-via-build-contract", []
        return original_item_text(item, matches, docx_paras)

    def add_runs(paragraph, text, source_runs, font_size, color=None, force_bold=False, italic=False, preserve_line_breaks=False):
        if source_runs and any(bool(run.get("__contract")) for run in source_runs):
            for src in source_runs:
                value = str(src.get("text") or "")
                if not value:
                    continue
                parts = value.split("\n") if preserve_line_breaks else [" ".join(value.split())]
                for index, part in enumerate(parts):
                    run = paragraph.add_run(part)
                    run.bold = bool(src.get("bold"))
                    run.italic = bool(src.get("italic"))
                    run.underline = bool(src.get("underline"))
                    run.font.superscript = bool(src.get("superscript"))
                    font = str(src.get("font") or "").strip()
                    if font:
                        run.font.name = font
                    size = src.get("size_pt")
                    run.font.size = Pt(float(size) if isinstance(size, (int, float)) else float(font_size))
                    pdf_rgb = _pdf_color(src.get("pdf_color"))
                    if pdf_rgb is not None:
                        run.font.color.rgb = pdf_rgb
                    legacy_module._set_run_language(run)
                    if preserve_line_breaks and index < len(parts) - 1:
                        run.add_break(legacy_module.WD_BREAK.LINE)
            return
        return original_add_runs(paragraph, text, source_runs, font_size, color, force_bold, italic, preserve_line_breaks)

    def contract_for_regions(region_ids: Any) -> dict[str, Any] | None:
        key = tuple(str(value) for value in region_ids or [])
        return region_map.get(key)

    def dominant(regions_by_id, region_ids, fallback):
        item = contract_for_regions(region_ids)
        if item:
            size = _font_size(item.get("pdfTypography") or {})
            if size is not None:
                return size
        return original_dominant(regions_by_id, region_ids, fallback)

    def maximum(regions_by_id, region_ids, fallback):
        item = contract_for_regions(region_ids)
        if item:
            profile = ((item.get("pdfTypography") or {}).get("fontSizePt") or {}).get("profile") or []
            values = []
            for row in profile:
                try:
                    values.append(float(row.get("value")))
                except (TypeError, ValueError):
                    pass
            if values:
                return max(values)
            size = _font_size(item.get("pdfTypography") or {})
            if size is not None:
                return size
        return original_max(regions_by_id, region_ids, fallback)

    def line_height(regions_by_id, region_ids, fallback):
        item = contract_for_regions(region_ids)
        if item:
            value = (((item.get("wordParagraph") or {}).get("geometry") or {}).get("lineHeightPt"))
            try:
                if value is not None and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return original_line_height(regions_by_id, region_ids, fallback)

    def flow_gap(raw_gap, item, body_size, gap_scale):
        spacing = ((item.get("__wordParagraph") or {}).get("spacing") or {})
        value = spacing.get("observedGapBeforePt")
        try:
            if value is not None:
                gap = max(0.0, float(value))
                return {
                    "raw_gap_pt": round(gap, 2),
                    "scaled_gap_pt": round(gap, 2),
                    "applied_gap_pt": round(gap, 2),
                    "gap_clamped": False,
                    "gap_quantized": False,
                    "gap_policy": "build-contract-pdf-observed-gap-before",
                }
        except (TypeError, ValueError):
            pass
        return original_gap(raw_gap, item, body_size, gap_scale)

    legacy_module._item_text_and_runs = item_text_and_runs
    legacy_module._add_runs = add_runs
    legacy_module._dominant_span_size = dominant
    legacy_module._max_span_size = maximum
    legacy_module._line_height = line_height
    legacy_module._word_flow_gap = flow_gap
    try:
        yield
    finally:
        legacy_module._item_text_and_runs = original_item_text
        legacy_module._add_runs = original_add_runs
        legacy_module._dominant_span_size = original_dominant
        legacy_module._max_span_size = original_max
        legacy_module._line_height = original_line_height
        legacy_module._word_flow_gap = original_gap
