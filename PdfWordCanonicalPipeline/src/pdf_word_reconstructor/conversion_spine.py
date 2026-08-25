from __future__ import annotations

from collections import Counter
import re
from typing import Any

from .common import compact_text


TRUST_MODEL = "markdown-first/pdf-guided/docx-secondary/output-audited"


def _docx_number(value: Any) -> int | None:
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else None


def _build_items(build_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in (build_report or {}).get("pages", []) or []:
        page_no = page.get("page")
        for item in page.get("items", []) or []:
            row = dict(item)
            row["page"] = page_no
            items.append(row)
    return items


def _build_docx_output_index(items: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        for paragraph_id in item.get("docx_paragraphs", []) or []:
            number = _docx_number(paragraph_id)
            if number is not None:
                result.setdefault(number, []).append(item)
    return result


def _page_output_items(items: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        try:
            page = int(item.get("page"))
        except Exception:
            continue
        result.setdefault(page, []).append(item)
    return result


def _docx_donor_by_index(docx_donor_map: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in (docx_donor_map or {}).get("paragraphs", []) or []:
        try:
            result[int(item.get("index"))] = item
        except Exception:
            continue
    return result


def _markdown_text(record: dict[str, Any]) -> str:
    for key in ("text", "latex", "captionText", "textPreview"):
        value = str(record.get(key) or "").strip()
        if value:
            return compact_text(value, 900)
    return ""


def _source_path(record: dict[str, Any]) -> str | None:
    value = record.get("source")
    return str(value) if value else None


def _survival_by_id(content_audit: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    survival = ((content_audit or {}).get("markdown_survival") or {}).get("problemElements", []) or []
    return {str(item.get("id") or ""): item for item in survival if item.get("id")}


def _decisions_by_markdown_id(fidelity_report: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in (fidelity_report or {}).get("userDecisionQueue", []) or []:
        seen: set[str] = set()
        for key in ("markdownId", "id"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("mdel-"):
                if value in seen:
                    continue
                seen.add(value)
                result.setdefault(value, []).append(item)
    return result


def _spine_item_by_id(markdown_pdf_spine: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in (markdown_pdf_spine or {}).get("items", []) or []
        if item.get("id")
    }


def _record_selected(record: dict[str, Any], spine_ids: set[str], selected_pages: set[int]) -> bool:
    record_id = str(record.get("id") or "")
    if record_id in spine_ids:
        return True
    page = record.get("page")
    return isinstance(page, int) and page in selected_pages


def _output_from_docx_evidence(
    record: dict[str, Any],
    docx_output_index: dict[int, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    evidence = record.get("docxEvidence") or {}
    number = _docx_number(evidence.get("paragraphId"))
    if number is None:
        return None
    output_items = docx_output_index.get(number, [])
    if not output_items:
        return None
    item = output_items[0]
    return {
        "status": "included",
        "evidence": "docx-paragraph-used-in-output",
        "page": item.get("page"),
        "outputItemId": item.get("id"),
        "source": item.get("source"),
        "docxParagraphs": item.get("docx_paragraphs", []),
        "mathCount": item.get("math_count", item.get("native_math_count", 0)),
    }


def _output_from_pdf_witness(
    spine_item: dict[str, Any] | None,
    output_by_page: dict[int, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    if not spine_item:
        return None
    page = spine_item.get("pdfPage")
    region = spine_item.get("pdfRegion")
    if not page:
        return None
    try:
        page_no = int(page)
    except Exception:
        return None
    for item in output_by_page.get(page_no, []):
        if region and item.get("id") == region:
            return {
                "status": "included",
                "evidence": "pdf-region-built-in-output",
                "page": page_no,
                "outputItemId": item.get("id"),
                "source": item.get("source"),
                "docxParagraphs": item.get("docx_paragraphs", []),
                "mathCount": item.get("math_count", item.get("native_math_count", 0)),
            }
    if spine_item.get("status") in {"strong", "medium", "page-hint", "position-hint"}:
        return {
            "status": "probable",
            "evidence": "markdown-position-witness" if spine_item.get("status") == "position-hint" else "pdf-witness-page-present",
            "page": page_no,
            "outputItemId": region,
            "source": None,
            "docxParagraphs": [],
            "mathCount": 0,
        }
    return None


def _docx_donor(record: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any] | None:
    evidence = record.get("docxEvidence") or {}
    if not evidence:
        return None
    return {
        "matched": bool(evidence.get("matched")),
        "paragraphId": evidence.get("paragraphId"),
        "paragraphIndex": evidence.get("paragraphIndex"),
        "score": evidence.get("score"),
        "status": evidence.get("status"),
        "suggestedType": evidence.get("suggestedType"),
        "ommlCount": evidence.get("ommlCount"),
        "drawingCount": evidence.get("drawingCount"),
        "usedInOutput": bool(output and output.get("evidence") == "docx-paragraph-used-in-output"),
    }


def _docx_donor_from_map(
    record: dict[str, Any],
    output: dict[str, Any] | None,
    donor_by_index: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    evidence = record.get("docxEvidence") or {}
    number = evidence.get("paragraphIndex")
    if number is None:
        number = _docx_number(evidence.get("paragraphId"))
    if number is None and output:
        for paragraph_id in output.get("docxParagraphs", []) or []:
            number = _docx_number(paragraph_id)
            if number is not None:
                break
    if number is None:
        return _docx_donor(record, output)
    donor = donor_by_index.get(int(number))
    if not donor:
        return _docx_donor(record, output)
    return {
        "matched": True,
        "paragraphId": donor.get("id"),
        "paragraphIndex": donor.get("index"),
        "score": evidence.get("score"),
        "status": evidence.get("status") or ("usedInOutput" if output else None),
        "suggestedType": evidence.get("suggestedType") or donor.get("donorType"),
        "donorType": donor.get("donorType"),
        "ommlCount": donor.get("ommlCount"),
        "drawingCount": donor.get("drawingCount"),
        "ommlSignature": donor.get("ommlSignature"),
        "usedInOutput": bool(output and int(number) in {
            value for value in (_docx_number(pid) for pid in output.get("docxParagraphs", []) or []) if value is not None
        }),
        "pdfLinkCount": len(donor.get("pdfLinks", []) or []),
        "markdownLinkCount": len(donor.get("markdownLinks", []) or []),
    }


def _outcome(
    record: dict[str, Any],
    spine_item: dict[str, Any] | None,
    output: dict[str, Any] | None,
    survival: dict[str, Any] | None,
    decisions: list[dict[str, Any]],
) -> tuple[str, bool, str]:
    if survival and survival.get("status") == "missing" and survival.get("survivalDecisionEligible") is True:
        return "missing-risk", True, "markdown-survival"
    if survival and survival.get("status") == "weak" and survival.get("survivalDecisionEligible") is True:
        return "weak-survival", True, "markdown-survival"
    if output:
        source = str(output.get("source") or "")
        if source == "docx-native-omml" or int(output.get("mathCount") or 0) > 0:
            return "native-word-math", False, "output"
        if source.startswith("page-crop"):
            return "visual-fallback", False, "output"
        if output.get("status") == "included":
            return "included", False, "output"
        return "probable-included", False, "output"
    if spine_item and spine_item.get("status") in {"strong", "medium", "page-hint", "position-hint"}:
        return "pdf-witness-confirmed", False, "pdf-witness"
    if spine_item and spine_item.get("status") == "weak":
        return "diagnostic-only", False, "weak-pdf-witness"
    if decisions:
        return "legacy-review-only", False, "legacy-report-diagnostic"
    return "unresolved-map", False, "no-hard-failure"


def build_conversion_spine(
    markdown_element_map: dict[str, Any] | None,
    markdown_pdf_spine: dict[str, Any] | None,
    page_structure: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
    content_audit: dict[str, Any] | None,
    fidelity_report: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = list((markdown_element_map or {}).get("records", []) or [])
    spine_by_id = _spine_item_by_id(markdown_pdf_spine)
    selected_pages = {
        int(page)
        for page in (markdown_pdf_spine or {}).get("selectedPages", []) or []
        if str(page).isdigit()
    }
    output_items = _build_items(build_report)
    docx_output_index = _build_docx_output_index(output_items)
    output_by_page = _page_output_items(output_items)
    donor_by_index = _docx_donor_by_index(docx_donor_map)
    survival_lookup = _survival_by_id(content_audit)
    decision_lookup = _decisions_by_markdown_id(fidelity_report)

    rows: list[dict[str, Any]] = []
    outcome_counts: Counter[str] = Counter()
    type_counts: Counter[str] = Counter()
    decision_count = 0
    decision_rows: list[dict[str, Any]] = []

    for record in records:
        record_id = str(record.get("id") or "")
        if not _record_selected(record, set(spine_by_id), selected_pages):
            continue
        spine_item = spine_by_id.get(record_id)
        output = _output_from_docx_evidence(record, docx_output_index)
        if output is None:
            output = _output_from_pdf_witness(spine_item, output_by_page)
        survival = survival_lookup.get(record_id)
        decisions = decision_lookup.get(record_id, [])
        outcome, decision_required, reason = _outcome(record, spine_item, output, survival, decisions)
        if decision_required:
            decision_count += 1
        kind = str(record.get("type") or "")
        type_counts[kind] += 1
        outcome_counts[outcome] += 1
        row = {
            "id": record_id,
            "orderIndex": record.get("orderIndex"),
            "type": kind,
            "source": _source_path(record),
            "line": record.get("line"),
            "markdown": {
                "pageHint": record.get("page"),
                "pageConfidence": record.get("pageConfidence"),
                "text": _markdown_text(record),
                "latex": record.get("latex"),
            },
            "pdfWitness": {
                "status": (spine_item or {}).get("status"),
                "outcome": (spine_item or {}).get("manifestOutcome"),
                "page": (spine_item or {}).get("pdfPage"),
                "region": (spine_item or {}).get("pdfRegion"),
                "score": (spine_item or {}).get("score"),
                "bbox": (spine_item or {}).get("bbox"),
                "text": compact_text((spine_item or {}).get("pdfText", ""), 420),
            } if spine_item else None,
            "docxDonor": _docx_donor_from_map(record, output, donor_by_index),
            "output": output,
            "survival": {
                "status": survival.get("status"),
                "score": survival.get("score"),
                "scorePdf": survival.get("scorePdf"),
                "scoreDocx": survival.get("scoreDocx"),
                "decisionEligible": survival.get("survivalDecisionEligible"),
            } if survival else None,
            "review": {
                "decisionRequired": decision_required,
                "reason": reason,
                "legacyDecisionCount": len(decisions),
                "questions": [item.get("question") for item in decisions if item.get("question")],
            },
            "outcome": outcome,
        }
        rows.append(row)
        if decision_required:
            decision_rows.append({
                "id": record_id,
                "type": kind,
                "page": (spine_item or {}).get("pdfPage") or record.get("page"),
                "outcome": outcome,
                "reason": reason,
                "question": (
                    "Το Markdown στοιχείο φαίνεται να λείπει ή να έχει ασθενή επιβίωση στο output. Να προστεθεί/διορθωθεί ή υπάρχει ήδη αλλού;"
                    if outcome in {"missing-risk", "weak-survival"}
                    else "Χρειάζεται έλεγχος αυτού του Markdown στοιχείου."
                ),
                "markdownText": row["markdown"].get("text"),
                "pdfText": (row.get("pdfWitness") or {}).get("text"),
                "survival": row.get("survival"),
            })

    total = len(rows)
    included = sum(
        count for key, count in outcome_counts.items()
        if key in {"native-word-math", "visual-fallback", "included", "probable-included", "pdf-witness-confirmed"}
    )
    return {
        "version": "conversion-spine-0.1",
        "truthModel": TRUST_MODEL,
        "policy": "Every selected Markdown element gets one row and one outcome. PDF witness confirms placement; DOCX is a donor; output/content audit decide whether a user decision is really needed.",
        "summary": {
            "markdownRecordCount": len(records),
            "selectedRowCount": total,
            "includedOrWitnessedCount": included,
            "coverage": round(included / total, 5) if total else 1.0,
            "decisionRequiredCount": decision_count,
            "decisionQueueCount": len(decision_rows),
            "outcomeCounts": dict(outcome_counts),
            "typeCounts": dict(type_counts),
            "selectedPages": sorted(selected_pages),
        },
        "decisionQueue": decision_rows,
        "rows": rows,
        "sourceReports": {
            "markdownElementMapVersion": (markdown_element_map or {}).get("version"),
            "markdownPdfSpineVersion": (markdown_pdf_spine or {}).get("version"),
            "pageStructureVersion": (page_structure or {}).get("version"),
            "fidelityReportVersion": (fidelity_report or {}).get("version"),
            "docxDonorMapVersion": (docx_donor_map or {}).get("version"),
        },
    }
