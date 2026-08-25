from __future__ import annotations

from collections import Counter
from typing import Any


BUILD_CONTRACT_VERSION = "build-contract-0.1"


def _clean_text(value: Any) -> str:
    return str(value or "")


def _output_kind(markdown_type: Any, layout_contract: dict[str, Any]) -> str:
    markdown_type = str(markdown_type or "").strip().lower()
    role = str(((layout_contract.get("styleHint") or {}).get("role")) or "").strip().lower()
    if markdown_type in {"image", "figure"} or role == "visual":
        return "visual"
    if markdown_type in {"display_equation", "equation"} or role == "math":
        return "equation"
    if markdown_type in {"table"}:
        return "table"
    if markdown_type in {"list", "list_item", "ordered_list", "unordered_list"}:
        return "list-item"
    if role == "heading" or markdown_type in {"heading", "title"}:
        return "heading"
    if role == "caption" or markdown_type == "caption":
        return "caption"
    if role == "callout":
        return "callout"
    return "paragraph"


def _authority(row: dict[str, Any]) -> dict[str, Any]:
    layout_contract = row.get("layoutContract") or {}
    donor = row.get("docxDonor") or None
    return {
        "content": {
            "source": "markdown",
            "markdownId": row.get("markdownId"),
            "text": _clean_text(row.get("markdownText")),
        },
        "layout": {
            "source": "pdf-via-page-layout-spine",
            "page": layout_contract.get("page"),
            "pageBox": layout_contract.get("pageBox"),
            "layoutMode": layout_contract.get("layoutMode"),
            "column": layout_contract.get("column"),
            "box": layout_contract.get("box"),
            "placement": layout_contract.get("placement"),
        },
        "typography": {
            "source": "pdf-via-page-layout-spine",
            "styleHint": layout_contract.get("styleHint"),
            "policy": {
                "fontSize": "pdf-span-dominant-size",
                "lineHeight": "pdf-line-pitch",
                "fontFamily": "pdf-span-when-reliable",
                "emphasis": "pdf-span-when-reliable",
            },
        },
        "nativeDonor": {
            "source": "docx-donor-map" if donor else None,
            "donor": donor,
            "allowedUses": ["omml", "native-word-object", "verified-style-hint"] if donor else [],
            "contentAuthority": False,
            "layoutAuthority": False,
        },
    }


def build_build_contract(page_layout_spine: dict[str, Any] | None) -> dict[str, Any]:
    spine = page_layout_spine or {}
    rows = list(spine.get("rows") or [])
    items: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()

    for index, row in enumerate(rows):
        layout_contract = row.get("layoutContract") or {}
        markdown_id = str(row.get("markdownId") or "")
        markdown_text = _clean_text(row.get("markdownText"))
        layout_status = str(layout_contract.get("status") or "")
        output_kind = _output_kind(row.get("markdownType"), layout_contract)

        unresolved: list[str] = []
        if not markdown_id:
            unresolved.append("missing-markdown-id")
        if layout_status != "usable":
            unresolved.append("missing-layout-contract")
        if output_kind not in {"visual", "equation", "table"} and not markdown_text.strip():
            unresolved.append("missing-markdown-content")

        status = "ready" if not unresolved else "unresolved"
        status_counts[status] += 1

        layout = row.get("layout") or {}
        items.append({
            "id": f"build-{index:05d}",
            "status": status,
            "unresolved": unresolved,
            "markdownId": markdown_id,
            "markdownType": row.get("markdownType"),
            "markdownOrder": row.get("markdownOrder"),
            "outputKind": output_kind,
            "authority": _authority(row),
            "placement": {
                "page": layout.get("page"),
                "slotId": layout.get("slotId"),
                "slotSource": layout.get("slotSource"),
                "columnIndex": layout.get("columnIndex"),
                "columnRole": layout.get("columnRole"),
                "flowOrder": layout.get("flowOrder"),
                "wordFlowOrder": layout.get("wordFlowOrder"),
                "bbox": layout.get("bbox"),
                "spanning": bool(layout.get("spanning")),
                "policy": layout_contract.get("placement"),
            },
            "layoutContract": layout_contract,
            "docxDonor": row.get("docxDonor"),
            "pdfWitness": row.get("pdfWitness"),
        })

    ready = int(status_counts.get("ready", 0))
    unresolved = int(status_counts.get("unresolved", 0))
    return {
        "version": BUILD_CONTRACT_VERSION,
        "policy": {
            "contentAuthority": "markdown",
            "layoutAuthority": "pdf",
            "typographyAuthority": "pdf",
            "docxRole": "native-donor-only",
            "builderRole": "execute-contract-no-rematching",
            "silentFallback": "forbidden",
        },
        "pageSetup": (spine.get("layoutPreflight") or {}).get("pageSetupEstimate"),
        "layoutPreflight": spine.get("layoutPreflight"),
        "summary": {
            "itemCount": len(items),
            "readyCount": ready,
            "unresolvedCount": unresolved,
            "readyCoverage": round(ready / len(items), 5) if items else 1.0,
            "statusCounts": dict(status_counts),
        },
        "items": items,
    }
