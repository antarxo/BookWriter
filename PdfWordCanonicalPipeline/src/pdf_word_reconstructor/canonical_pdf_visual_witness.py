from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical_evidence_fusion import _intersection_fraction, _pdf_visual_containers


VERSION = "canonical-pdf-visual-witness-0.2"


def _pdf_page_count(pdf_path: Path) -> int | None:
    try:
        import fitz  # type: ignore
    except Exception:
        return None
    doc = fitz.open(str(pdf_path))
    count = len(doc)
    doc.close()
    return count


def resolve_pdf_page_mapping(
    pdf_path: Path,
    line_pages: list[int],
    physical_page: int,
) -> dict[str, Any]:
    pages = sorted({int(value) for value in line_pages if int(value) > 0})
    count = _pdf_page_count(Path(pdf_path))
    if count is None:
        return {"status": "unresolved", "reason": "pdf-page-count-unavailable"}
    if physical_page not in pages:
        return {
            "status": "unresolved",
            "reason": "physical-page-not-present-in-lines-pages",
            "pdfPageCount": count,
            "linePages": pages,
        }
    if count == len(pages):
        index = pages.index(physical_page)
        return {
            "status": "resolved", "mode": "subset-ordinal",
            "pdfPageIndex": index, "pdfPageNumber": index + 1,
            "physicalPage": physical_page, "pdfPageCount": count,
            "linePages": pages, "confidence": "high",
        }
    if physical_page <= count:
        index = physical_page - 1
        return {
            "status": "resolved", "mode": "physical-page-number",
            "pdfPageIndex": index, "pdfPageNumber": physical_page,
            "physicalPage": physical_page, "pdfPageCount": count,
            "linePages": pages, "confidence": "high",
        }
    return {
        "status": "unresolved",
        "reason": "pdf-page-count-incompatible-with-lines-page-range",
        "physicalPage": physical_page,
        "pdfPageCount": count,
        "linePages": pages,
    }


def apply_pdf_visual_witness(
    report: dict[str, Any],
    line_map: dict[str, Any],
    pdf_path: Path,
    physical_page: int,
) -> dict[str, Any]:
    """Attach the existing PDF drawing evidence through explicit page mapping."""
    line_pages = [
        int(page.get("page") or 0)
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    ]
    mapping = resolve_pdf_page_mapping(Path(pdf_path), line_pages, physical_page)
    report["pdfVisualWitness"] = {
        "version": VERSION,
        "mapping": mapping,
        "status": "blocked" if mapping.get("status") != "resolved" else "pending",
        "wordRealization": None,
    }
    if mapping.get("status") != "resolved":
        return report

    page_index = int(mapping["pdfPageIndex"])
    containers, profile = _pdf_visual_containers(Path(pdf_path), page_index)
    source_page = next(
        (page for page in line_map.get("pages", []) or [] if int(page.get("page") or 0) == physical_page),
        None,
    ) or {}
    try:
        width_px = float(source_page.get("page_width_px") or 0.0)
        height_px = float(source_page.get("page_height_px") or 0.0)
        width_pt = float(profile.get("pageWidthPt") or 0.0)
        height_pt = float(profile.get("pageHeightPt") or 0.0)
    except (TypeError, ValueError):
        width_px = height_px = width_pt = height_pt = 0.0

    if not profile.get("available") or min(width_px, height_px, width_pt, height_pt) <= 0:
        report["pdfVisualWitness"].update({
            "status": "blocked", "reason": "page-scale-unavailable", "profile": profile,
        })
        return report

    scale_x = width_pt / width_px
    scale_y = height_pt / height_px
    blocks = [
        block for block in report.get("blocks", []) or []
        if int((block.get("pageAssignment") or {}).get("physicalPage") or 0) == physical_page
    ]
    groups = list(report.get("groups", []) or [])
    attached_containers: list[dict[str, Any]] = []
    created_groups: list[str] = []

    for ordinal, container in enumerate(containers):
        row = dict(container)
        row["physicalPage"] = physical_page
        row["pdfPageIndex"] = page_index
        members: list[str] = []
        for block in blocks:
            bbox = (block.get("geometry") or {}).get("bboxPx")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            block_pt = [
                float(bbox[0]) * scale_x, float(bbox[1]) * scale_y,
                float(bbox[2]) * scale_x, float(bbox[3]) * scale_y,
            ]
            if _intersection_fraction(block_pt, row["bboxPt"]) >= 0.78:
                members.append(str(block.get("id") or ""))
        row["memberBlockIds"] = members
        attached_containers.append(row)
        if not members:
            continue

        group_id = f"pdf-visual-group-{physical_page}-{ordinal:04d}"
        groups.append({
            "id": group_id,
            "type": "visual-container",
            "physicalPage": physical_page,
            "memberBlockIds": members,
            "bboxPt": row["bboxPt"],
            "evidence": [{
                "source": "pdf-drawing-enclosure",
                "containerId": row.get("id"),
                "mappingMode": mapping.get("mode"),
                "confidence": "medium",
            }],
            "wordRealization": None,
        })
        created_groups.append(group_id)
        for block in blocks:
            if str(block.get("id") or "") not in members:
                continue
            relation_groups = block.setdefault("relations", {}).setdefault("belongsToGroups", [])
            if group_id not in relation_groups:
                relation_groups.append(group_id)
            container_ids = block.setdefault("visualEvidence", {}).setdefault("pdfContainerIds", [])
            if row.get("id") not in container_ids:
                container_ids.append(row.get("id"))

    report["groups"] = groups
    report["pdfContainers"] = attached_containers
    summary = report.setdefault("summary", {})
    summary["groupCount"] = len(groups)
    summary["wordDecisionCount"] = (
        sum(block.get("wordRealization") is not None for block in report.get("blocks", []) or [])
        + sum(group.get("wordRealization") is not None for group in groups)
    )
    report["pdfVisualWitness"].update({
        "status": "observed",
        "profile": profile,
        "scale": {"xPtPerPx": scale_x, "yPtPerPx": scale_y},
        "containerCount": len(attached_containers),
        "groupCount": len(created_groups),
        "createdGroupIds": created_groups,
        "policy": (
            "existing PDF drawing extraction is reused as visual enclosure evidence; "
            "page mapping is explicit and semantic/topology/Word decisions are unchanged"
        ),
    })
    return report


__all__ = ["VERSION", "resolve_pdf_page_mapping", "apply_pdf_visual_witness"]
