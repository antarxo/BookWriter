from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median
from typing import Any


VERSION = "layout-contract-normalizer-0.1"
_TEXT_KINDS = {"paragraph", "heading", "caption", "callout", "list"}


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return [round(part, 3) for part in box]


def _slot_aliases(value: Any) -> list[str]:
    slot = str(value or "")
    if not slot:
        return []
    aliases = [slot]
    if slot.startswith("flow-"):
        aliases.append(slot[5:])
    return list(dict.fromkeys(aliases))


def _page_map_items(page_structure: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        if page_no <= 0:
            continue
        for collection_name in ("flow", "callouts", "headers", "footers", "banners"):
            for item in page.get(collection_name, []) or []:
                for alias in _slot_aliases(item.get("id")):
                    result[(page_no, alias)] = item
                for alias in _slot_aliases(item.get("visual_group_id")):
                    result.setdefault((page_no, alias), item)
    return result


def _line_boxes(runs: list[dict[str, Any]]) -> list[list[float]]:
    grouped: dict[int, list[list[float]]] = defaultdict(list)
    fallback_index = 0
    for run in runs:
        box = _bbox(run.get("bboxPt"))
        if not box:
            continue
        try:
            line_index = int(run.get("sourceLineIndex"))
        except (TypeError, ValueError):
            line_index = fallback_index
            fallback_index += 1
        grouped[line_index].append(box)
    boxes: list[list[float]] = []
    for line_index in sorted(grouped):
        rows = grouped[line_index]
        boxes.append([
            round(min(row[0] for row in rows), 3),
            round(min(row[1] for row in rows), 3),
            round(max(row[2] for row in rows), 3),
            round(max(row[3] for row in rows), 3),
        ])
    return boxes


def _line_pitch(boxes: list[list[float]]) -> tuple[float | None, str | None]:
    if len(boxes) >= 2:
        centers = [(box[1] + box[3]) / 2.0 for box in boxes]
        diffs = [centers[index] - centers[index - 1] for index in range(1, len(centers)) if centers[index] > centers[index - 1]]
        if diffs:
            return round(float(median(diffs)), 3), "pdf-span-line-centre-pitch"
    if len(boxes) == 1:
        height = boxes[0][3] - boxes[0][1]
        if height > 0:
            return round(float(height), 3), "pdf-single-line-span-box-height"
    return None, None


def _weighted_ratio(runs: list[dict[str, Any]], key: str) -> float:
    total = 0
    yes = 0
    for run in runs:
        text = str(run.get("text") or "")
        weight = max(1, len(text.strip())) if text.strip() else 0
        if not weight:
            continue
        total += weight
        style = run.get("style") if isinstance(run.get("style"), dict) else {}
        if bool(style.get(key)):
            yes += weight
    return round(yes / total, 5) if total else 0.0


def _font_profile(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[float] = Counter()
    for run in runs:
        value = run.get("fontSizePt")
        text = str(run.get("text") or "")
        weight = max(1, len(text.strip())) if text.strip() else 0
        try:
            size = float(value)
        except (TypeError, ValueError):
            continue
        if size > 0 and weight:
            counts[size] += weight
    return [
        {"value": round(size, 3), "weightedChars": int(weight)}
        for size, weight in sorted(counts.items())
    ]


def _typography_from_text_style(text_style: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(text_style, dict) or not text_style:
        return None
    summary = text_style.get("summary") if isinstance(text_style.get("summary"), dict) else {}
    dominant = summary.get("dominant") if isinstance(summary.get("dominant"), dict) else {}
    runs = [run for run in (text_style.get("runs") or []) if isinstance(run, dict)]
    boxes = _line_boxes(runs)
    pitch, pitch_source = _line_pitch(boxes)
    family = dominant.get("fontFamily")
    size = dominant.get("fontSizePt")
    color = dominant.get("color")
    try:
        size_value = float(size) if size is not None else None
    except (TypeError, ValueError):
        size_value = None
    confidence = "high" if size_value and boxes else ("medium" if size_value else "none")
    return {
        "confidence": confidence,
        "source": "page_structure.textStyleMap/pdf-native-spans",
        "fontFamily": {"dominant": family},
        "fontSizePt": {"dominant": round(size_value, 3) if size_value else None, "profile": _font_profile(runs)},
        "color": {"dominant": color},
        "emphasis": {
            "boldRatio": _weighted_ratio(runs, "bold"),
            "italicRatio": _weighted_ratio(runs, "italic"),
            "underlineRatio": _weighted_ratio(runs, "underline"),
            "superscriptRatio": _weighted_ratio(runs, "superscript"),
        },
        "lineCount": len(boxes),
        "lineBoxes": boxes,
        "linePitch": {"medianPt": pitch, "source": pitch_source},
        "evidence": {
            "authority": text_style.get("authority"),
            "sourceRegionIds": list(text_style.get("sourceRegionIds") or []),
            "textStyleMapVersion": text_style.get("version"),
        },
    }


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == {} or value == []


def _merge_typography(existing: dict[str, Any], mapped: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    result = dict(existing or {})
    filled: list[str] = []
    if str(result.get("confidence") or "none") == "none" and str(mapped.get("confidence") or "none") != "none":
        result["confidence"] = mapped.get("confidence")
        filled.append("confidence")
    if _missing(result.get("source")):
        result["source"] = mapped.get("source")
        filled.append("source")
    for key in ("fontFamily", "fontSizePt", "color", "emphasis", "lineCount", "lineBoxes", "linePitch", "evidence"):
        current = result.get(key)
        incoming = mapped.get(key)
        if _missing(current) and not _missing(incoming):
            result[key] = incoming
            filled.append(key)
            continue
        if isinstance(current, dict) and isinstance(incoming, dict):
            merged = dict(current)
            changed = False
            for subkey, subvalue in incoming.items():
                if _missing(merged.get(subkey)) and not _missing(subvalue):
                    merged[subkey] = subvalue
                    changed = True
            if changed:
                result[key] = merged
                filled.append(key)
    return result, filled


def normalize_layout_contracts(result: dict[str, Any], page_structure: dict[str, Any]) -> dict[str, Any]:
    """Complete renderer-facing contracts only from evidence already frozen in page maps.

    This function does not re-read PDF/DOCX/Lines and does not invent defaults.
    Missing downstream fields are populated from page_structure.textStyleMap and
    page-map geometry. Anything still unavailable remains unavailable for the
    build-contract validator to reject before Word rendering.
    """
    page_items = _page_map_items(page_structure)
    normalized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for row in result.get("rows", []) or []:
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        try:
            page_no = int(layout.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        slot_id = str(layout.get("slotId") or "")
        map_item = None
        for alias in _slot_aliases(slot_id):
            map_item = page_items.get((page_no, alias))
            if map_item is not None:
                break
        if map_item is None:
            continue

        mapped_typography = _typography_from_text_style(map_item.get("textStyleMap") or {})
        filled: list[str] = []
        if mapped_typography:
            typography, typography_filled = _merge_typography(
                row.get("pdfTypography") if isinstance(row.get("pdfTypography"), dict) else {},
                mapped_typography,
            )
            row["pdfTypography"] = typography
            filled.extend(f"pdfTypography.{name}" for name in typography_filled)

            word = row.get("wordParagraph") if isinstance(row.get("wordParagraph"), dict) else {}
            geometry = word.get("geometry") if isinstance(word.get("geometry"), dict) else {}
            pitch = ((typography.get("linePitch") or {}).get("medianPt"))
            if _missing(geometry.get("lineHeightPt")) and pitch is not None:
                geometry["lineHeightPt"] = pitch
                geometry["lineHeightSource"] = ((typography.get("linePitch") or {}).get("source"))
                filled.append("wordParagraph.geometry.lineHeightPt")
            if _missing(geometry.get("lineBoxes")) and typography.get("lineBoxes"):
                geometry["lineBoxes"] = list(typography.get("lineBoxes") or [])
                geometry["lineCount"] = typography.get("lineCount")
                filled.append("wordParagraph.geometry.lineBoxes")
            word["geometry"] = geometry
            word["typography"] = typography

            frame = word.get("frame") if isinstance(word.get("frame"), dict) else None
            if frame is not None and _missing(frame.get("bboxPt")):
                box = _bbox(map_item.get("bbox")) or _bbox(layout.get("bbox"))
                if box:
                    frame["bboxPt"] = box
                    frame["source"] = "page-structure-slot-geometry"
                    word["frame"] = frame
                    filled.append("wordParagraph.frame.bboxPt")
            row["wordParagraph"] = word

            contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
            contract["wordParagraph"] = word
            row["layoutContract"] = contract

        output_kind = str(row.get("markdownType") or "").strip().lower()
        if filled:
            normalized.append({
                "markdownId": row.get("markdownId"),
                "page": page_no,
                "slotId": slot_id,
                "mapItemId": map_item.get("id"),
                "fields": filled,
                "source": "page_structure.textStyleMap",
            })

        if output_kind not in {"image", "figure", "display_equation", "equation", "table", "latex_table"}:
            typography = row.get("pdfTypography") if isinstance(row.get("pdfTypography"), dict) else {}
            word = row.get("wordParagraph") if isinstance(row.get("wordParagraph"), dict) else {}
            geometry = word.get("geometry") if isinstance(word.get("geometry"), dict) else {}
            missing: list[str] = []
            if ((typography.get("fontSizePt") or {}).get("dominant")) is None:
                missing.append("pdfTypography.fontSizePt.dominant")
            if geometry.get("lineHeightPt") is None:
                missing.append("wordParagraph.geometry.lineHeightPt")
            if (row.get("layoutContract") or {}).get("placement") == "positioned-text-frame":
                frame = word.get("frame") if isinstance(word.get("frame"), dict) else {}
                if not _bbox(frame.get("bboxPt")):
                    missing.append("wordParagraph.frame.bboxPt")
            if missing:
                unresolved.append({
                    "markdownId": row.get("markdownId"),
                    "page": page_no,
                    "slotId": slot_id,
                    "mapItemId": map_item.get("id"),
                    "missing": missing,
                })

    audit = {
        "version": VERSION,
        "normalizedCount": len(normalized),
        "unresolvedRendererFieldCount": len(unresolved),
        "normalized": normalized,
        "unresolved": unresolved,
        "policy": "renderer fields may be completed only from already-frozen page-map evidence; no PDF/DOCX/Lines reread and no invented defaults",
    }
    result["layoutContractNormalization"] = audit
    result.setdefault("summary", {})["layoutContractNormalization"] = {
        "normalizedCount": len(normalized),
        "unresolvedRendererFieldCount": len(unresolved),
    }
    return audit


__all__ = ["normalize_layout_contracts"]
