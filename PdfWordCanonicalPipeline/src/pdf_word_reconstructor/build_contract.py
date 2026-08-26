from __future__ import annotations

from collections import Counter
from typing import Any


BUILD_CONTRACT_VERSION = "build-contract-0.2"


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


def _content_text(row: dict[str, Any]) -> str:
    content = row.get("contentContract") if isinstance(row.get("contentContract"), dict) else {}
    authoritative = row.get("authoritativeContent") if isinstance(row.get("authoritativeContent"), dict) else {}
    for value in (
        content.get("text"),
        authoritative.get("text"),
        row.get("markdownText"),
    ):
        text = str(value or "")
        if text:
            return text
    return ""


def _unresolved_reasons(row: dict[str, Any], output_kind: str) -> list[str]:
    reasons: list[str] = []
    markdown_id = str(row.get("markdownId") or "")
    layout_contract = row.get("layoutContract") or {}
    word_paragraph = row.get("wordParagraph") or {}
    typography = row.get("pdfTypography") or {}
    content = row.get("contentContract") or {}

    if not markdown_id:
        reasons.append("missing-markdown-id")
    if str(layout_contract.get("status") or "") != "usable":
        reasons.append("missing-layout-contract")
    if not isinstance(content, dict) or not content:
        reasons.append("missing-content-contract")
    if output_kind not in {"visual", "equation", "table"} and not _content_text(row).strip():
        reasons.append("missing-markdown-content")
    if output_kind in {"paragraph", "heading", "caption", "callout", "list"}:
        if not isinstance(word_paragraph, dict) or not word_paragraph:
            reasons.append("missing-word-paragraph-contract")
        if str((typography or {}).get("confidence") or "none") == "none":
            reasons.append("missing-pdf-typography")
    return reasons


def build_build_contract(page_layout_spine: dict[str, Any] | None) -> dict[str, Any]:
    spine = page_layout_spine or {}
    rows = list(spine.get("rows") or [])
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    output_counts: Counter[str] = Counter()

    for index, row in enumerate(rows):
        output_kind = _output_kind(row)
        unresolved = _unresolved_reasons(row, output_kind)
        status = "ready" if not unresolved else "unresolved"
        status_counts[status] += 1
        output_counts[output_kind] += 1
        reason_counts.update(unresolved)

        layout = row.get("layout") or {}
        items.append({
            "id": f"build-{index:05d}",
            "status": status,
            "unresolved": unresolved,
            "markdownId": row.get("markdownId"),
            "markdownType": row.get("markdownType"),
            "markdownOrder": row.get("markdownOrder"),
            "outputKind": output_kind,
            "content": row.get("contentContract") or {},
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
                "typography": "pdf-via-markdown-pdf-spine",
                "nativeDonor": "docx-donor-map-secondary-only" if row.get("docxDonor") else None,
            },
        })

    ready = int(status_counts.get("ready", 0))
    unresolved = int(status_counts.get("unresolved", 0))
    return {
        "version": BUILD_CONTRACT_VERSION,
        "sourcePageLayoutSpineVersion": spine.get("version"),
        "policy": {
            "role": "assembler-and-completeness-gate-only",
            "contentAuthority": "markdown",
            "layoutAuthority": "pdf",
            "typographyAuthority": "pdf",
            "docxRole": "native-donor-only",
            "builderRole": "execute-contract-no-rematching",
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
            "outputKindCounts": dict(output_counts),
        },
        "items": items,
    }
