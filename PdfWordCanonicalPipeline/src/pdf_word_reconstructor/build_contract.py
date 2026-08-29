from __future__ import annotations

from collections import Counter
from typing import Any


BUILD_CONTRACT_VERSION = "build-contract-0.5-renderer-schema"


def _output_kind(row: dict[str, Any]) -> str:
    markdown_type = str(row.get("markdownType") or "").strip().lower()
    layout_contract = row.get("layoutContract") or {}
    role = str(((layout_contract.get("styleHint") or {}).get("role")) or "").strip().lower()
    if markdown_type in {"image", "figure"} or role == "visual":
        return "visual"
    if markdown_type in {"display_equation", "equation"} or role == "math":
        return "equation"
    if markdown_type in {"table", "latex_table"}:
        return "table"
    if markdown_type in {"list", "latex_list", "list_item", "ordered_list", "unordered_list"}:
        return "list"
    if role == "heading" or markdown_type in {"heading", "title"}:
        return "heading"
    if role == "caption" or markdown_type == "caption":
        return "caption"
    if role == "callout":
        return "callout"
    return "paragraph"


def _authoritative_payload(row: dict[str, Any]) -> dict[str, Any]:
    explicit = row.get("contentContract")
    if isinstance(explicit, dict) and explicit:
        return explicit
    authoritative = row.get("authoritativeContent")
    if isinstance(authoritative, dict) and authoritative:
        return authoritative
    return {}


def _content_text(row: dict[str, Any]) -> str:
    content = _authoritative_payload(row)
    authoritative = row.get("authoritativeContent") if isinstance(row.get("authoritativeContent"), dict) else {}
    for value in (
        content.get("text"),
        content.get("plainText"),
        authoritative.get("text"),
        authoritative.get("plainText"),
        row.get("markdownText"),
    ):
        text = str(value or "")
        if text:
            return text
    return ""


def _valid_bbox(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _unresolved_reasons(row: dict[str, Any], output_kind: str) -> list[str]:
    reasons: list[str] = []
    markdown_id = str(row.get("markdownId") or "")
    layout_contract = row.get("layoutContract") or {}
    word_paragraph = row.get("wordParagraph") or {}
    typography = row.get("pdfTypography") or {}
    content = _authoritative_payload(row)

    if not markdown_id:
        reasons.append("missing-markdown-id")
    if str(layout_contract.get("status") or "") != "usable":
        reasons.append("missing-layout-contract")
    if not content:
        reasons.append("missing-markdown-authoritative-payload")
    if output_kind not in {"visual", "equation", "table"} and not _content_text(row).strip():
        reasons.append("missing-markdown-content")

    if output_kind in {"paragraph", "heading", "caption", "callout", "list"}:
        if not isinstance(word_paragraph, dict) or not word_paragraph:
            reasons.append("missing-word-paragraph-contract")
        if str((typography or {}).get("confidence") or "none") == "none":
            reasons.append("missing-pdf-typography")

        # Renderer schema: the Word renderer consumes these exact fields. They
        # must be present before rendering; a generic 'typography exists' flag is
        # not sufficient and must never allow a later crash.
        font_size = ((typography.get("fontSizePt") or {}).get("dominant")) if isinstance(typography, dict) else None
        if not _positive_number(font_size):
            reasons.append("missing-renderer-font-size")

        geometry = word_paragraph.get("geometry") if isinstance(word_paragraph.get("geometry"), dict) else {}
        if not _positive_number(geometry.get("lineHeightPt")):
            reasons.append("missing-renderer-line-height")

        positioned = str(layout_contract.get("placement") or "") == "positioned-text-frame"
        if output_kind == "callout" or positioned:
            frame = word_paragraph.get("frame") if isinstance(word_paragraph.get("frame"), dict) else {}
            if not _valid_bbox(frame.get("bboxPt")):
                reasons.append("missing-renderer-frame-bbox")

    return list(dict.fromkeys(reasons))


def build_build_contract(page_layout_spine: dict[str, Any] | None) -> dict[str, Any]:
    spine = page_layout_spine or {}
    rows = list(spine.get("rows") or [])
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()
    unresolved_by_kind: Counter[str] = Counter()
    unresolved_by_reason_and_kind: Counter[str] = Counter()
    unresolved_samples: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        output_kind = _output_kind(row)
        unresolved = _unresolved_reasons(row, output_kind)
        status = "ready" if not unresolved else "unresolved"
        status_counts[status] += 1
        output_counts[output_kind] += 1
        reason_counts.update(unresolved)
        if unresolved:
            unresolved_by_kind[output_kind] += 1
            for reason in unresolved:
                unresolved_by_reason_and_kind[f"{reason}:{output_kind}"] += 1
            if len(unresolved_samples) < 24:
                layout = row.get("layout") or {}
                typography = row.get("pdfTypography") or {}
                unresolved_samples.append({
                    "markdownId": row.get("markdownId"),
                    "markdownType": row.get("markdownType"),
                    "outputKind": output_kind,
                    "reasons": list(unresolved),
                    "page": layout.get("page"),
                    "slotId": layout.get("slotId"),
                    "slotSource": layout.get("slotSource"),
                    "bbox": layout.get("bbox") or (row.get("pdfGeometry") or {}).get("bbox"),
                    "layoutStatus": (row.get("layoutContract") or {}).get("status"),
                    "typographySource": typography.get("source"),
                    "typographyConfidence": typography.get("confidence"),
                    "textPreview": _content_text(row)[:140],
                })

        layout = row.get("layout") or {}
        content_payload = _authoritative_payload(row)
        items.append({
            "id": f"build-{index:05d}",
            "status": status,
            "unresolved": unresolved,
            "markdownId": row.get("markdownId"),
            "markdownType": row.get("markdownType"),
            "markdownOrder": row.get("markdownOrder"),
            "outputKind": output_kind,
            "content": content_payload,
            "authoritativeContent": row.get("authoritativeContent") or {},
            "rawMarkdown": str(row.get("rawMarkdown") or ""),
            "placement": {
                "page": layout.get("page"),
                "slotId": layout.get("slotId"),
                "parentSlotId": layout.get("parentSlotId"),
                "slotSource": layout.get("slotSource"),
                "columnIndex": layout.get("columnIndex"),
                "columnRole": layout.get("columnRole"),
                "flowOrder": layout.get("flowOrder"),
                "wordFlowOrder": layout.get("wordFlowOrder"),
                "bbox": layout.get("bbox"),
                "spanning": bool(layout.get("spanning")),
                "policy": (row.get("layoutContract") or {}).get("placement"),
            },
            "layoutContract": row.get("layoutContract") or {},
            "wordParagraph": row.get("wordParagraph") or {},
            "pdfTypography": row.get("pdfTypography") or {},
            "pdfGeometry": row.get("pdfGeometry") or {},
            "pdfWitness": row.get("pdfWitness") or {},
            "docxDonor": row.get("docxDonor"),
            "authority": {
                "content": "markdown",
                "layout": "pdf-via-page-layout-spine",
                "typography": "pdf-via-page-maps",
                "nativeDonor": "docx-donor-map-secondary-only" if row.get("docxDonor") else None,
            },
        })

    ready = int(status_counts.get("ready", 0))
    unresolved = int(status_counts.get("unresolved", 0))
    return {
        "version": BUILD_CONTRACT_VERSION,
        "sourcePageLayoutSpineVersion": spine.get("version"),
        "policy": {
            "role": "assembler-and-renderer-schema-completeness-gate",
            "contentAuthority": "markdown",
            "layoutAuthority": "pdf",
            "typographyAuthority": "pdf-page-maps",
            "docxRole": "native-donor-only",
            "builderRole": "execute-contract-no-rematching",
            "rendererSchemaValidatedBeforeBuild": True,
            "rematching": "forbidden",
            "reinterpretation": "forbidden",
            "silentFallback": "forbidden",
        },
        "pageSetup": (spine.get("layoutPreflight") or {}).get("pageSetupEstimate"),
        "layoutPreflight": spine.get("layoutPreflight"),
        "layoutOrderBySlot": spine.get("layoutOrderBySlot") or {},
        "summary": {
            "itemCount": len(items),
            "readyCount": ready,
            "unresolvedCount": unresolved,
            "readyCoverage": round(ready / len(items), 5) if items else 1.0,
            "statusCounts": dict(status_counts),
            "unresolvedReasonCounts": dict(reason_counts),
            "unresolvedByOutputKind": dict(unresolved_by_kind),
            "unresolvedByReasonAndKind": dict(unresolved_by_reason_and_kind),
            "unresolvedSamples": unresolved_samples,
            "outputKindCounts": dict(output_counts),
        },
        "items": items,
    }
