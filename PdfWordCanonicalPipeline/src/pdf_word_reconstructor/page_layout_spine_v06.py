from __future__ import annotations

from typing import Any

from .page_layout_spine_v04 import build_page_layout_spine as _build_v05


VERSION = "page-layout-spine-0.6"


def _callout_lookup(page_structure: dict[str, Any] | None) -> dict[tuple[int, str], dict[str, Any]]:
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for page in (page_structure or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for callout in page.get("callouts", []) or []:
            callout_id = str(callout.get("id") or "")
            if page_no and callout_id:
                result[(page_no, callout_id)] = callout
    return result


def _style_from_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    evidence = evidence if isinstance(evidence, dict) else {}
    status = str(evidence.get("status") or "unresolved")
    confidence = str(evidence.get("confidence") or "none")
    usable = status == "matched" and confidence in {"high", "medium"}
    stroke = evidence.get("stroke") if isinstance(evidence.get("stroke"), dict) else {}
    fill = evidence.get("fill") if isinstance(evidence.get("fill"), dict) else {}
    if usable:
        border = {
            "status": stroke.get("status") or "none-or-not-painted",
            "source": "pdf-vector-drawing",
            "color": stroke.get("color"),
            "widthPt": stroke.get("widthPt"),
            "opacity": stroke.get("opacity"),
            "dashes": stroke.get("dashes"),
        }
        frame_fill = {
            "status": fill.get("status") or "none-or-not-painted",
            "source": "pdf-vector-drawing",
            "color": fill.get("color"),
            "opacity": fill.get("opacity"),
        }
    else:
        border = {
            "status": "unresolved-no-confident-vector-match",
            "source": None,
            "color": None,
            "widthPt": None,
            "opacity": None,
            "dashes": None,
        }
        frame_fill = {
            "status": "unresolved-no-confident-vector-match",
            "source": None,
            "color": None,
            "opacity": None,
        }
    return {
        "evidenceStatus": status,
        "evidenceConfidence": confidence,
        "drawingId": evidence.get("drawingId"),
        "drawingBBoxPt": evidence.get("drawingBBoxPt"),
        "matchScore": evidence.get("score"),
        "border": border,
        "fill": frame_fill,
        "vectorEvidence": evidence,
        "rendererPolicy": "apply-only-confident-pdf-vector-style",
    }


def build_page_layout_spine(
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _build_v05(markdown_pdf_spine, page_structure, docx_donor_map)
    callouts = _callout_lookup(page_structure)
    frame_rows = 0
    styled_rows = 0
    unresolved_rows = 0

    for row in result.get("rows", []) or []:
        word = row.get("wordParagraph") if isinstance(row.get("wordParagraph"), dict) else {}
        frame = word.get("frame") if isinstance(word.get("frame"), dict) else None
        if frame is None:
            continue
        frame_rows += 1
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        page_no = int(layout.get("page") or 0)
        slot_id = str(layout.get("slotId") or "")
        callout = callouts.get((page_no, slot_id))
        evidence = (callout or {}).get("frame_evidence") if isinstance(callout, dict) else None
        style = _style_from_evidence(evidence)
        frame.update(style)
        word["frame"] = frame
        row["wordParagraph"] = word
        layout_contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        layout_contract["wordParagraph"] = word
        row["layoutContract"] = layout_contract
        if style.get("evidenceStatus") == "matched" and style.get("evidenceConfidence") in {"high", "medium"}:
            styled_rows += 1
        else:
            unresolved_rows += 1

    result["version"] = VERSION
    result["policy"] = (
        str(result.get("policy") or "")
        + " Callout border/fill authority comes only from matched PyMuPDF vector drawings; unresolved style remains absent rather than guessed."
    ).strip()
    summary = result.setdefault("summary", {})
    summary["frameStyleEvidence"] = {
        "frameRowCount": frame_rows,
        "vectorStyledRowCount": styled_rows,
        "unresolvedFrameStyleRowCount": unresolved_rows,
        "source": "page_structure.callouts[].frame_evidence",
        "policy": "apply-only-confident-pdf-vector-style",
    }
    return result
