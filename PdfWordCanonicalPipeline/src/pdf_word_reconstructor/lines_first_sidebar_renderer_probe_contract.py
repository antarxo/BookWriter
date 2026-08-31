from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .lines_first_layout_cluster_probe_v3_contract import build_lines_first_layout_cluster_probe_v3_contract

VERSION = "lines-first-sidebar-renderer-probe-0.1"


def _page_of_row(row: dict[str, Any]) -> int:
    try:
        return int((row.get("layout") or {}).get("page") or 0)
    except (TypeError, ValueError):
        return 0


def _single_page_spine(spine: dict[str, Any], page_no: int) -> dict[str, Any]:
    out = deepcopy(spine)
    out["rows"] = [deepcopy(row) for row in (spine.get("rows", []) or []) if _page_of_row(row) == page_no]
    order_map = spine.get("layoutOrderBySlot") or {}
    prefix = f"{page_no}:"
    if any(":" in str(key) for key in order_map):
        out["layoutOrderBySlot"] = {key: value for key, value in order_map.items() if str(key).startswith(prefix)}
    out["version"] = VERSION
    out["policy"] = (
        "Single-source-page diagnostic renderer spine. Content model is unchanged; high-confidence sidebar items "
        "are rendered through the existing native Word positioned-frame path."
    )
    return out


def build_lines_first_sidebar_renderer_probe_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    """Prepare one-page renderer inputs for high-confidence sidebar testing.

    The v3 lane-cluster model remains the authority for the sidebar decision. Narrow
    width alone never creates a frame. For this diagnostic only, each high-confidence
    sidebar flow item is marked `spanning=True` so the preserved native builder sends
    it through its borderless page-relative `w:framePr` path with wrap-around.

    Pages are returned as independent renderer jobs. This intentionally avoids mixing
    page-relative frame validation with the current free-flow single-section pagination
    policy, which is a separate architectural problem.
    """
    base = deepcopy(build_lines_first_layout_cluster_probe_v3_contract(
        Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt
    ))
    evidence = base["layoutClusterEvidence"]
    pages_by_no = {
        int(page.get("page") or 0): page
        for page in (base["pageStructure"].get("pages", []) or [])
    }

    jobs: list[dict[str, Any]] = []
    promoted_total = 0
    for report in evidence.get("pageReports", []) or []:
        page_no = int(report.get("page") or 0)
        page = pages_by_no.get(page_no)
        if page is None:
            continue
        high = [
            candidate for candidate in (report.get("sidebarCalloutCandidates", []) or [])
            if str(candidate.get("confidence") or "") == "high"
        ]
        sidebar_by_id = {str(candidate.get("sideItemId") or ""): candidate for candidate in high}
        promoted: list[dict[str, Any]] = []
        page_copy = deepcopy(page)
        for item in page_copy.get("flow", []) or []:
            item_id = str(item.get("id") or "")
            candidate = sidebar_by_id.get(item_id)
            if candidate is None:
                continue
            item["spanning"] = True
            item["positioned_role"] = "sidebar-callout"
            item["positioned_role_source"] = VERSION
            item["sidebarEvidence"] = deepcopy(candidate)
            promoted.append({
                "id": item_id,
                "bbox": deepcopy(item.get("bbox")),
                "side": candidate.get("side"),
                "mainLaneId": candidate.get("mainLaneId"),
                "confidence": candidate.get("confidence"),
                "rendererPath": "existing-borderless-spanning-text-frame",
            })
        promoted_total += len(promoted)
        jobs.append({
            "page": page_no,
            "pageStructure": {
                **{key: deepcopy(value) for key, value in base["pageStructure"].items() if key != "pages"},
                "pages": [page_copy],
                "version": VERSION,
                "source": "lines-first-sidebar-renderer-probe-single-page",
            },
            "pageLayoutSpine": _single_page_spine(base["pageLayoutSpine"], page_no),
            "promotedSidebars": promoted,
        })

    return {
        "version": VERSION,
        "rendererDecision": "diagnostic-single-source-page-jobs",
        "contentModel": "lines-first+mmd-span+stable-dedup",
        "layoutEvidenceVersion": evidence.get("version"),
        "narrowImpliesFloating": False,
        "trueMulticolumnRendering": False,
        "sidebarRenderer": "existing-native-word-w:framePr-via-spanning-text-frame",
        "sidebarBorderFill": "none-added-by-probe",
        "paginationPolicy": "one-source-page-per-independent-docx-diagnostic-only",
        "jobs": jobs,
        "summary": {
            "pageCount": len(jobs),
            "sidebarPromotedCount": promoted_total,
            "pagesWithSidebarFrames": [job["page"] for job in jobs if job["promotedSidebars"]],
            "pagesWithoutSidebarFrames": [job["page"] for job in jobs if not job["promotedSidebars"]],
        },
    }


__all__ = ["build_lines_first_sidebar_renderer_probe_contract"]
