from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from docx import Document

from .native_builder import build_native_page_document


VERSION = "source-neutral-builder-adapter-0.1"


def _neutral_pdf_analysis(page_structure: dict[str, Any]) -> dict[str, Any]:
    pages = []
    for page in page_structure.get("pages", []) or []:
        pages.append({
            "page": int(page.get("page") or 0),
            "width_pt": page.get("width_pt"),
            "height_pt": page.get("height_pt"),
            "regions": [],
            "drawings": [],
        })
    return {
        "version": VERSION,
        "source": "source-neutral-adapter",
        "pages": pages,
    }


def _drop_unrenderable_visuals(page_structure: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    materialized = deepcopy(page_structure)
    omitted: list[dict[str, Any]] = []
    for page in materialized.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        kept_groups = []
        dropped_ids: set[str] = set()
        for group in page.get("visual_groups", []) or []:
            if group.get("crop_path"):
                kept_groups.append(group)
                continue
            group_id = str(group.get("id") or "")
            if group_id:
                dropped_ids.add(group_id)
            omitted.append({
                "page": page_no,
                "id": group_id or None,
                "kind": group.get("kind"),
                "bbox": group.get("bbox"),
                "reason": "canonical visual slot has no renderable asset bytes",
            })
        page["visual_groups"] = kept_groups
        if dropped_ids:
            page["flow"] = [
                item for item in page.get("flow", []) or []
                if str(item.get("visual_group_id") or "") not in dropped_ids
            ]
    return materialized, omitted


def build_source_neutral_document(
    *,
    page_structure: dict[str, Any],
    page_layout_spine: dict[str, Any],
    output_path: Path,
    body_size_override: float | None = None,
) -> dict[str, Any]:
    """Execute the existing canonical native builder without source evidence donors.

    This adapter contributes no content/layout evidence. It supplies only neutral
    compatibility objects required by the preserved renderer signature. Missing
    visual asset bytes are explicitly omitted and reported rather than invented.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    materialized_structure, omitted_visuals = _drop_unrenderable_visuals(page_structure)
    pdf_analysis = _neutral_pdf_analysis(materialized_structure)
    alignment = {
        "summary": {},
        "matches": [],
        "source": "source-neutral-adapter",
    }

    with TemporaryDirectory(prefix="bookwriter-neutral-") as temp_dir:
        blank_docx = Path(temp_dir) / "neutral_source.docx"
        Document().save(blank_docx)
        docx_analysis = {
            "source": str(blank_docx),
            "paragraphs": [],
            "sections": [],
            "sourcePolicy": "technical-empty-carrier-no-evidence",
        }
        report = build_native_page_document(
            pdf_analysis,
            materialized_structure,
            alignment,
            docx_analysis,
            {},
            output_path,
            body_size_override=body_size_override,
            font_scale=1.0,
            gap_scale=0.0,
            body_line_spacing_multiple=None,
            docx_donor_map=None,
            page_layout_spine=page_layout_spine,
            flow_mode="free",
        )

    if not isinstance(report, dict):
        report = {"legacyReport": report}
    report["sourceNeutralAdapter"] = {
        "version": VERSION,
        "evidenceInputs": {
            "pdf": False,
            "markdown": False,
            "docx": False,
            "docxDonor": False,
        },
        "technicalBlankDocxCarrier": True,
        "omittedVisualCount": len(omitted_visuals),
        "omittedVisuals": omitted_visuals,
        "policy": (
            "The blank DOCX exists only to satisfy the preserved renderer function signature and contributes no evidence. "
            "No missing visual bytes are fabricated."
        ),
    }
    return report


__all__ = ["build_source_neutral_document"]
