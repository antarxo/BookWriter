from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Any

from .common import normalize_text

_MATH_CHARS = set("=+-−×·⋅/<>≤≥∑∫√∞≈≠^_λνΔΕEhncπμωαβγδφψ()[]{}")
_CAPTION_PREFIXES = ("σχήμα ", "εικόνα ", "πίνακας ", "διάγραμμα ")
_HEADER_WORDS = ("χημεια", "κβαντικοι αριθμοι", "τευχος")
_FOOTER_WORDS = ("κονδυλης", "λατζωνης")


def _bbox_overlap_ratio(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = map(float, a)
    bx0, by0, bx1, by1 = map(float, b)
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    return inter / area


def _expanded(bbox: list[float], pad: float = 4.0) -> list[float]:
    x0, y0, x1, y1 = map(float, bbox)
    return [x0 - pad, y0 - pad, x1 + pad, y1 + pad]


def _spans(region: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for line in region.get("lines", [])
        for s in line.get("spans", [])
        if str(s.get("text", "")).strip()
    ]


def _stats(region: dict[str, Any], page_width: float, page_height: float) -> dict[str, Any]:
    text = str(region.get("text", ""))
    compact = re.sub(r"\s+", " ", text).strip()
    normalized = normalize_text(text)
    spans = _spans(region)
    sizes = [float(s.get("size_pt") or 0.0) for s in spans]
    max_size = max(sizes, default=0.0)
    weighted_size_num = sum(float(s.get("size_pt") or 0.0) * max(1, len(str(s.get("text", "")).strip())) for s in spans)
    weighted_size_den = sum(max(1, len(str(s.get("text", "")).strip())) for s in spans)
    weighted_size = weighted_size_num / weighted_size_den if weighted_size_den else 0.0
    fonts = [str(s.get("font", "")).lower() for s in spans]
    colors = [str(s.get("color", "#000000")).lower() for s in spans]
    flags = [int(s.get("flags", 0)) for s in spans]
    bold = any("bold" in f or (fl & 16) for f, fl in zip(fonts, flags))
    italic = any("italic" in f or "oblique" in f or (fl & 2) for f, fl in zip(fonts, flags))
    colored = any(c not in {"#000000", "#231f20", "#1a1a1a"} for c in colors)

    x0, y0, x1, y1 = map(float, region.get("bbox", [0, 0, 0, 0]))
    width = max(0.0, x1 - x0)
    height = max(0.0, y1 - y0)
    letters = sum(ch.isalpha() for ch in compact)
    digits = sum(ch.isdigit() for ch in compact)
    private_use = sum(unicodedata.category(ch) == "Co" for ch in compact)
    math_chars = sum(ch in _MATH_CHARS for ch in compact)
    punctuation = sum(unicodedata.category(ch).startswith("P") for ch in compact)
    char_count = max(1, len(compact))
    alpha_ratio = letters / char_count
    math_ratio = (math_chars + digits + private_use) / char_count
    line_count = max(1, len(region.get("lines", [])))
    all_caps_letters = [ch for ch in compact if ch.isalpha()]
    upper_ratio = (
        sum(ch.isupper() for ch in all_caps_letters) / len(all_caps_letters)
        if all_caps_letters
        else 0.0
    )
    return {
        "text": text,
        "compact": compact,
        "normalized": normalized,
        "spans": spans,
        "max_size": round(max_size, 2),
        "weighted_size": round(weighted_size, 2),
        "bold": bool(bold),
        "italic": bool(italic),
        "colored": bool(colored),
        "bbox": [x0, y0, x1, y1],
        "width": width,
        "height": height,
        "x0_ratio": x0 / page_width if page_width else 0.0,
        "x1_ratio": x1 / page_width if page_width else 0.0,
        "y0_ratio": y0 / page_height if page_height else 0.0,
        "y1_ratio": y1 / page_height if page_height else 0.0,
        "width_ratio": width / page_width if page_width else 0.0,
        "alpha_ratio": alpha_ratio,
        "math_ratio": math_ratio,
        "private_use": private_use,
        "punctuation": punctuation,
        "line_count": line_count,
        "upper_ratio": upper_ratio,
        "char_count": len(compact),
    }


def _leading_number(text: str) -> str | None:
    m = re.match(r"^\s*(\d+(?:[.,]\d+)*)\s*[.)]?", text)
    return m.group(1) if m else None


def _looks_like_formula_token_soup(st: dict[str, Any]) -> bool:
    """Catch formula glyph stacks whose PDF text extraction lost operators.

    In some PDFs a perfectly normal displayed equation is extracted as several
    one-character tokens (for example ``h c E E λ / i f``) with no reliable
    bounding-box order. Treating that as prose creates visible garbage in Word.
    The heuristic is deliberately conservative: it requires a short multi-line
    block dominated by one- or two-character scientific symbols.
    """
    if st["line_count"] < 2 or st["char_count"] > 90:
        return False
    tokens = [re.sub(r"[^0-9A-Za-zΑ-Ωα-ωλνΔΕπμω∞=+−\-*/·⋅]", "", token) for token in st["compact"].split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 4:
        return False
    short_tokens = sum(len(token) <= 2 for token in tokens)
    known_symbols = sum(
        token in {"h", "c", "E", "Ei", "Ef", "n", "i", "f", "λ", "ν", "ΔE", "ΔΕ", "∞", "J"}
        or any(ch in "λνΔΕ∞=+−-*/·⋅" for ch in token)
        for token in tokens
    )
    return short_tokens / len(tokens) >= 0.72 and known_symbols >= 3


def _is_equation(st: dict[str, Any]) -> bool:
    text = st["compact"]
    if not text:
        return False
    if st["private_use"] > 0 and (st["char_count"] <= 160 or st["alpha_ratio"] < 0.55):
        return True
    if st["char_count"] <= 10 and st["alpha_ratio"] < 0.25 and st["math_ratio"] >= 0.35:
        return True
    if _looks_like_formula_token_soup(st):
        return True
    # A short, centred mathematical relation or a multi-line symbol stack.
    has_relation = any(op in text for op in ("=", "≈", "≤", "≥", "∑", "∫", "√", "∞"))
    short = st["char_count"] <= 120
    low_prose = st["alpha_ratio"] < 0.46
    if short and has_relation and (low_prose or st["math_ratio"] >= 0.26):
        return True
    if st["line_count"] >= 3 and st["math_ratio"] >= 0.34 and st["alpha_ratio"] < 0.42:
        return True
    return False


def _looks_like_heading(st: dict[str, Any], body_size: float | None) -> bool:
    text = st["normalized"]
    if not text or st["char_count"] > 180 or st["line_count"] > 4:
        return False
    if body_size and st["max_size"] >= body_size * 1.12:
        return True
    numbered = _leading_number(st["compact"]) is not None
    keyword = text.startswith(("θεματα θεωριας", "εφαρμογη ", "παραδειγμα ", "ερωτησεις "))
    if st["colored"] and (st["bold"] or numbered or keyword):
        return True
    if st["bold"] and (numbered or keyword) and st["line_count"] <= 4:
        return True
    return False


def _flow_zone(st: dict[str, Any], semantic_type: str) -> str:
    if semantic_type in {"heading", "banner", "caption", "body"}:
        return "main"
    if semantic_type == "callout":
        return "left_sidebar" if st["x1_ratio"] <= 0.40 else "right_sidebar"
    if st["width_ratio"] < 0.33 and st["x1_ratio"] <= 0.42:
        return "left_sidebar"
    if st["width_ratio"] < 0.33 and st["x0_ratio"] >= 0.64:
        return "right_sidebar"
    return "main"


def classify_pdf_regions(pdf_analysis: dict[str, Any], body_size: float | None = None) -> dict[str, Any]:
    """Attach deterministic semantic labels to PDF text regions.

    The classifier intentionally separates prose that should align to DOCX paragraphs
    from PDF-only visual fragments such as diagram labels, headers, footers and
    formula glyph stacks. This prevents bogus fuzzy matches from looking like data.
    """
    counts: Counter[str] = Counter()
    alignable_counts: Counter[str] = Counter()

    for page in pdf_analysis.get("pages", []):
        page_width = float(page.get("width_pt") or 595.0)
        page_height = float(page.get("height_pt") or 842.0)
        image_boxes = [r.get("bbox", [0, 0, 0, 0]) for r in page.get("regions", []) if r.get("type") == "image"]

        for region in page.get("regions", []):
            if region.get("type") != "text":
                continue
            st = _stats(region, page_width, page_height)
            text_norm = st["normalized"]
            reasons: list[str] = []
            semantic_type = "body"
            confidence = 0.72

            likely_header = (
                st["y1_ratio"] <= 0.075
                and st["max_size"] <= 12.5
                and (
                    st["upper_ratio"] >= 0.65
                    or any(word in text_norm for word in _HEADER_WORDS)
                )
            )
            likely_footer = (
                st["y0_ratio"] >= 0.90
                or (
                    st["y0_ratio"] >= 0.84
                    and (any(word in text_norm for word in _FOOTER_WORDS) or "κονδ" in text_norm or "λατζ" in text_norm or re.search(r"(?:^|\s)\d{1,3}$", text_norm or "") is not None)
                )
            )
            overlaps_image = max((_bbox_overlap_ratio(st["bbox"], _expanded(box, 5.0)) for box in image_boxes), default=0.0)
            short_image_label = (
                st["char_count"] <= 80
                and st["line_count"] <= 3
                and overlaps_image >= 0.35
                and not text_norm.startswith(_CAPTION_PREFIXES)
            )
            narrow_edge_block = (
                st["width_ratio"] <= 0.25
                and st["char_count"] >= 28
                and (st["x1_ratio"] <= 0.34 or st["x0_ratio"] >= 0.68)
            )

            if likely_header:
                semantic_type, confidence = "header", 0.97
                reasons.append("top repeating header zone")
            elif likely_footer:
                semantic_type, confidence = "footer", 0.97
                reasons.append("bottom footer/page-number zone")
            elif st["max_size"] >= 15.5 and st["y0_ratio"] < 0.25:
                semantic_type, confidence = "banner", 0.93
                reasons.append("large title text in top banner zone")
            elif text_norm.startswith(_CAPTION_PREFIXES):
                semantic_type, confidence = "caption", 0.98
                reasons.append("caption prefix")
            elif _is_equation(st):
                semantic_type, confidence = "equation", 0.96
                reasons.append("symbol-dense mathematical region")
            elif short_image_label:
                semantic_type, confidence = "figure_label", 0.92
                reasons.append("short text overlapping diagram/image")
            elif _looks_like_heading(st, body_size):
                semantic_type, confidence = "heading", 0.90
                reasons.append("font/color/numbering heading pattern")
            elif narrow_edge_block:
                semantic_type, confidence = "callout", 0.88
                reasons.append("narrow prose block at page edge")
            elif st["char_count"] <= 2 and st["alpha_ratio"] < 0.5:
                semantic_type, confidence = "noise", 0.90
                reasons.append("isolated punctuation or glyph")
            else:
                semantic_type, confidence = "body", 0.78
                reasons.append("default flowing prose")

            alignable = semantic_type in {"body", "heading", "callout", "caption", "banner"}
            region["semantic"] = {
                "type": semantic_type,
                "confidence": round(confidence, 2),
                "alignable": alignable,
                "flow_zone": _flow_zone(st, semantic_type),
                "reasons": reasons,
                "stats": {
                    k: st[k]
                    for k in (
                        "max_size", "weighted_size", "bold", "italic", "colored",
                        "width_ratio", "x0_ratio", "x1_ratio", "y0_ratio", "y1_ratio",
                        "alpha_ratio", "math_ratio", "private_use", "line_count", "char_count",
                    )
                },
            }

        # Captions are often split into several adjacent PDF blocks. Propagate the
        # caption class to immediately following fragments on the same baseline flow.
        text_regions = [r for r in page.get("regions", []) if r.get("type") == "text"]
        text_regions.sort(key=lambda r: (float(r.get("bbox", [0, 0, 0, 0])[1]), float(r.get("bbox", [0, 0, 0, 0])[0])))
        previous = None
        for current in text_regions:
            if previous is not None:
                prev_sem = previous.get("semantic", {})
                cur_sem = current.get("semantic", {})
                pb = previous.get("bbox", [0, 0, 0, 0])
                cb = current.get("bbox", [0, 0, 0, 0])
                gap = float(cb[1]) - float(pb[3])
                horizontal_overlap = max(0.0, min(float(pb[2]), float(cb[2])) - max(float(pb[0]), float(cb[0])))
                min_width = max(1.0, min(float(pb[2]) - float(pb[0]), float(cb[2]) - float(cb[0])))
                cur_stats = cur_sem.get("stats", {})
                if (
                    prev_sem.get("type") == "caption"
                    and cur_sem.get("type") == "body"
                    and int(cur_stats.get("char_count", 9999)) <= 180
                    and int(cur_stats.get("line_count", 99)) <= 4
                    and -1.0 <= gap <= 3.0
                    and horizontal_overlap / min_width >= 0.45
                ):
                    cur_sem["type"] = "caption"
                    cur_sem["alignable"] = True
                    cur_sem["confidence"] = 0.94
                    cur_sem["flow_zone"] = "main"
                    cur_sem.setdefault("reasons", []).append("continuation of adjacent caption block")
            previous = current

    # Count after propagation so the summary reflects final semantic labels.
    for page in pdf_analysis.get("pages", []):
        for region in page.get("regions", []):
            if region.get("type") != "text":
                continue
            sem = region.get("semantic", {})
            kind = sem.get("type", "body")
            counts[kind] += 1
            if sem.get("alignable", False):
                alignable_counts[kind] += 1

    return {
        "counts": dict(counts),
        "alignable_counts": dict(alignable_counts),
        "alignable_total": sum(alignable_counts.values()),
        "excluded_total": sum(counts.values()) - sum(alignable_counts.values()),
    }
