from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .region_classifier import classify_pdf_regions as _classify_v01


VERSION = "pdf-region-classifier-0.2"


def _looks_like_prose_false_positive(region: dict[str, Any]) -> bool:
    semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
    if str(semantic.get("type") or "") != "equation":
        return False
    stats = semantic.get("stats") if isinstance(semantic.get("stats"), dict) else {}
    text = re.sub(r"\s+", " ", str(region.get("text") or "")).strip()
    if not text:
        return False

    # Ordinary explanatory prose may legitimately contain ΔE, n=2, '=' and
    # numbers. Those tokens alone must not turn the whole sentence into an
    # equation region. Require a substantial natural-language word signal.
    words = re.findall(r"[A-Za-zΑ-Ωα-ωΆ-Ώά-ώ]{3,}", text)
    alpha_ratio = float(stats.get("alpha_ratio") or 0.0)
    math_ratio = float(stats.get("math_ratio") or 0.0)
    char_count = int(stats.get("char_count") or len(text))
    line_count = int(stats.get("line_count") or 1)

    if len(words) >= 7 and alpha_ratio >= 0.48 and math_ratio <= 0.28:
        return True
    if len(words) >= 10 and char_count >= 70 and math_ratio <= 0.34:
        return True
    if len(words) >= 6 and line_count <= 3 and char_count >= 55 and alpha_ratio >= 0.56:
        return True
    return False


def classify_pdf_regions(pdf_analysis: dict[str, Any], body_size: float | None = None) -> dict[str, Any]:
    _classify_v01(pdf_analysis, body_size=body_size)
    demoted = 0
    examples: list[dict[str, Any]] = []

    for page in pdf_analysis.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for region in page.get("regions", []) or []:
            if region.get("type") != "text" or not _looks_like_prose_false_positive(region):
                continue
            semantic = region.get("semantic") or {}
            prior = {
                "type": semantic.get("type"),
                "confidence": semantic.get("confidence"),
                "reasons": list(semantic.get("reasons") or []),
            }
            semantic["type"] = "body"
            semantic["confidence"] = 0.92
            semantic["alignable"] = True
            semantic["flow_zone"] = "main"
            semantic["reasons"] = ["equation-v0.1 rejected by prose-density guard"]
            semantic["previousClassification"] = prior
            region["semantic"] = semantic
            demoted += 1
            if len(examples) < 20:
                examples.append({
                    "page": page_no,
                    "regionId": region.get("id"),
                    "text": str(region.get("text") or "")[:220],
                    "stats": semantic.get("stats") or {},
                    "previous": prior,
                })

    counts: Counter[str] = Counter()
    alignable_counts: Counter[str] = Counter()
    for page in pdf_analysis.get("pages", []) or []:
        for region in page.get("regions", []) or []:
            if region.get("type") != "text":
                continue
            semantic = region.get("semantic") or {}
            kind = str(semantic.get("type") or "body")
            counts[kind] += 1
            if semantic.get("alignable"):
                alignable_counts[kind] += 1

    return {
        "version": VERSION,
        "counts": dict(counts),
        "alignableCounts": dict(alignable_counts),
        "proseEquationFalsePositiveDemotions": demoted,
        "demotionExamples": examples,
        "policy": "Do not classify explanatory prose as equation merely because it contains mathematical symbols or short inline relations.",
    }
