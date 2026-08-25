from __future__ import annotations

from collections import Counter
from typing import Any


DEFAULT_THRESHOLDS = {
    "markdownPdfCoverageMin": 0.85,
    "markdownPdfUnplacedMax": 0.10,
    "layoutContractCoverageMin": 0.85,
    "layoutSlotCollisionMax": 0.05,
    "conversionCoverageMin": 0.90,
    "conversionUnresolvedMax": 0.02,
}


def _summary(report: dict[str, Any] | None) -> dict[str, Any]:
    return (report or {}).get("summary", {}) or {}


def _ratio(count: int, total: int) -> float:
    return round(count / total, 5) if total else 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _markdown_pdf_page_mismatches(markdown_pdf_spine: dict[str, Any] | None) -> list[dict[str, Any]]:
    selected_pages = {
        _int(page, -1)
        for page in (markdown_pdf_spine or {}).get("selectedPages", []) or []
    }
    result: list[dict[str, Any]] = []
    for item in (markdown_pdf_spine or {}).get("items", []) or []:
        hint = item.get("markdownPageHint")
        pdf_page = item.get("pdfPage")
        if not isinstance(hint, int) or hint not in selected_pages:
            continue
        try:
            pdf_page_no = int(pdf_page)
        except (TypeError, ValueError):
            continue
        if pdf_page_no != hint:
            result.append({
                "id": item.get("id"),
                "type": item.get("type"),
                "markdownPageHint": hint,
                "pdfPage": pdf_page_no,
                "status": item.get("status"),
                "score": item.get("score"),
                "text": item.get("text"),
            })
    return result


def _page_source_counts(markdown_pdf_spine: dict[str, Any] | None) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in (markdown_pdf_spine or {}).get("items", []) or []:
        counts[str(item.get("pageHintSource") or "none")] += 1
    return dict(counts)


def _layout_slot_collisions(page_layout_spine: dict[str, Any] | None) -> list[dict[str, Any]]:
    slots: dict[str, list[dict[str, Any]]] = {}
    for row in (page_layout_spine or {}).get("rows", []) or []:
        layout = row.get("layout") or {}
        page = layout.get("page")
        slot_id = layout.get("slotId")
        if page is None or not slot_id:
            continue
        key = f"{page}:{slot_id}"
        slots.setdefault(key, []).append(row)
    collisions: list[dict[str, Any]] = []
    for key, rows in slots.items():
        if len(rows) <= 1:
            continue
        collisions.append({
            "slot": key,
            "count": len(rows),
            "markdownIds": [row.get("markdownId") for row in rows[:8]],
            "types": [row.get("markdownType") for row in rows[:8]],
            "texts": [row.get("markdownText") for row in rows[:3]],
        })
    return sorted(collisions, key=lambda item: int(item.get("count") or 0), reverse=True)


def build_mapping_fidelity(
    *,
    markdown_pdf_spine: dict[str, Any] | None,
    page_layout_spine: dict[str, Any] | None,
    conversion_spine: dict[str, Any] | None,
    thresholds: dict[str, float] | None = None,
    require_conversion: bool = True,
) -> dict[str, Any]:
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def fail(code: str, message: str, **data: Any) -> None:
        violations.append({"code": code, "message": message, **data})

    def warn(code: str, message: str, **data: Any) -> None:
        warnings.append({"code": code, "message": message, **data})

    md_summary = _summary(markdown_pdf_spine)
    layout_summary = _summary(page_layout_spine)
    conversion_summary = _summary(conversion_spine)
    conversion_outcomes = conversion_summary.get("outcomeCounts") or {}

    md_total = _int((markdown_pdf_spine or {}).get("itemCount"))
    md_placed = _int((markdown_pdf_spine or {}).get("placedCount"))
    md_weak = _int((markdown_pdf_spine or {}).get("weakCount"))
    md_unplaced = _int((markdown_pdf_spine or {}).get("unplacedCount"))
    md_coverage = _float((markdown_pdf_spine or {}).get("coverage"))

    layout_total = _int(layout_summary.get("rowCount"))
    layout_usable = _int(layout_summary.get("contractUsableCount"))
    layout_unplaced = _int(layout_summary.get("unplacedLayoutSlotCount"))
    layout_contract_coverage = _float(layout_summary.get("contractCoverage"))
    safe_flow = _int(layout_summary.get("safeFlowOrderingSlotCount"))
    slot_collisions = _layout_slot_collisions(page_layout_spine)
    collided_rows = sum(_int(item.get("count")) - 1 for item in slot_collisions)
    slot_collision_ratio = _ratio(collided_rows, layout_total)

    conversion_total = _int(conversion_summary.get("selectedRowCount"))
    conversion_included = _int(conversion_summary.get("includedOrWitnessedCount"))
    conversion_coverage = _float(conversion_summary.get("coverage"))
    unresolved_count = _int(conversion_outcomes.get("unresolved-map"))
    diagnostic_only_count = _int(conversion_outcomes.get("diagnostic-only"))

    page_mismatches = _markdown_pdf_page_mismatches(markdown_pdf_spine)
    page_source_counts = _page_source_counts(markdown_pdf_spine)
    neighbour_count = _int(page_source_counts.get("neighbor-same-page")) + _int(page_source_counts.get("neighbor-page-window"))

    if not markdown_pdf_spine:
        fail("markdown-pdf-spine-missing", "Δεν υπάρχει Markdown→PDF spine για έλεγχο πιστότητας.")
    elif md_total:
        if md_coverage < limits["markdownPdfCoverageMin"]:
            fail(
                "markdown-pdf-coverage-low",
                "Η χαρτογράφηση Markdown→PDF έχει χαμηλή κάλυψη.",
                coverage=md_coverage,
                minimum=limits["markdownPdfCoverageMin"],
                placed=md_placed,
                total=md_total,
            )
        if _ratio(md_unplaced, md_total) > limits["markdownPdfUnplacedMax"]:
            fail(
                "markdown-pdf-unplaced-high",
                "Πολλά Markdown στοιχεία δεν τοποθετήθηκαν στο PDF.",
                unplaced=md_unplaced,
                total=md_total,
                ratio=_ratio(md_unplaced, md_total),
                maximum=limits["markdownPdfUnplacedMax"],
            )
        if page_mismatches:
            warn(
                "markdown-pdf-page-mismatches",
                "Υπάρχουν Markdown page hints που οδηγούν σε διαφορετική PDF σελίδα.",
                count=len(page_mismatches),
                examples=page_mismatches[:8],
            )
        if neighbour_count:
            warn(
                "markdown-page-neighbor-inference-used",
                "Μέρος της χαρτογράφησης βασίστηκε σε γειτονικά Markdown στοιχεία.",
                count=neighbour_count,
                pageHintSources=page_source_counts,
            )

    if not page_layout_spine:
        fail("page-layout-spine-missing", "Δεν υπάρχει PDF/layout spine για έλεγχο θέσεων, στηλών και slots.")
    else:
        layout_preflight = (page_layout_spine or {}).get("layoutPreflight") or {}
        if not (layout_preflight.get("pageSetupEstimate") or {}):
            fail(
                "layout-preflight-missing",
                "Ο χάρτης layout δεν περιέχει προεκτίμηση σελιδοποίησης/περιθωρίων πριν από το Word render.",
            )
    if page_layout_spine and layout_total:
        if layout_contract_coverage < limits["layoutContractCoverageMin"]:
            fail(
                "layout-contract-coverage-low",
                "Η χαρτογράφηση PDF→layout slots έχει χαμηλή κάλυψη.",
                coverage=layout_contract_coverage,
                minimum=limits["layoutContractCoverageMin"],
                usable=layout_usable,
                total=layout_total,
                unplaced=layout_unplaced,
            )
        if slot_collision_ratio > limits["layoutSlotCollisionMax"]:
            fail(
                "layout-slot-collisions-high",
                "Πολλά Markdown στοιχεία χαρτογραφήθηκαν στο ίδιο PDF/layout slot.",
                collidedRows=collided_rows,
                total=layout_total,
                ratio=slot_collision_ratio,
                maximum=limits["layoutSlotCollisionMax"],
                examples=slot_collisions[:8],
            )
        elif slot_collisions:
            warn(
                "layout-slot-collisions-present",
                "Υπάρχουν πολλαπλές αντιστοιχίσεις στο ίδιο PDF/layout slot. Θέλει προσοχή γιατί μπορεί να αυξήσει το ύψος σελίδας.",
                collidedRows=collided_rows,
                total=layout_total,
                ratio=slot_collision_ratio,
                examples=slot_collisions[:8],
            )
        if safe_flow and _ratio(safe_flow, layout_total) < 0.50:
            warn(
                "safe-flow-ordering-low",
                "Λίγα layout slots είναι ασφαλή για flow ordering. Αυτό μπορεί να επηρεάσει τη σειρά και τη σελιδοποίηση.",
                safeFlowOrderingSlotCount=safe_flow,
                total=layout_total,
                ratio=_ratio(safe_flow, layout_total),
            )

    if not conversion_spine and require_conversion:
        fail("conversion-spine-missing", "Δεν υπάρχει conversion spine για έλεγχο Markdown→PDF→DOCX/output.")
    elif conversion_spine and conversion_total:
        if conversion_coverage < limits["conversionCoverageMin"]:
            fail(
                "conversion-coverage-low",
                "Η συνολική χαρτογράφηση Markdown→PDF→DOCX/output έχει χαμηλή κάλυψη.",
                coverage=conversion_coverage,
                minimum=limits["conversionCoverageMin"],
                includedOrWitnessed=conversion_included,
                total=conversion_total,
            )
        unresolved_ratio = _ratio(unresolved_count, conversion_total)
        if unresolved_ratio > limits["conversionUnresolvedMax"]:
            fail(
                "conversion-unresolved-high",
                "Πολλά στοιχεία μένουν unresolved στη conversion spine.",
                unresolved=unresolved_count,
                total=conversion_total,
                ratio=unresolved_ratio,
                maximum=limits["conversionUnresolvedMax"],
            )
        if diagnostic_only_count:
            warn(
                "conversion-diagnostic-only-present",
                "Υπάρχουν στοιχεία μόνο με ασθενή PDF witness. Παραμένουν διαγνωστικά, όχι αποφάσεις χρήστη.",
                diagnosticOnly=diagnostic_only_count,
            )

    status = "pass" if not violations else "fail"
    return {
        "version": "mapping-fidelity-0.1",
        "truthModel": "markdown-first/pdf-guided/docx-secondary/output-audited",
        "phase": "final" if require_conversion else "preflight",
        "status": status,
        "violationCount": len(violations),
        "warningCount": len(warnings),
        "thresholds": limits,
        "metrics": {
            "markdownPdf": {
                "total": md_total,
                "placed": md_placed,
                "weak": md_weak,
                "unplaced": md_unplaced,
                "coverage": md_coverage,
                "unplacedRatio": _ratio(md_unplaced, md_total),
                "pageHintSourceCounts": page_source_counts,
                "pageMismatchCount": len(page_mismatches),
            },
            "pageLayout": {
                "total": layout_total,
                "usableContracts": layout_usable,
                "unplaced": layout_unplaced,
                "contractCoverage": layout_contract_coverage,
                "slotCollisionCount": len(slot_collisions),
                "collidedRows": collided_rows,
                "slotCollisionRatio": slot_collision_ratio,
                "safeFlowOrderingSlotCount": safe_flow,
                "safeFlowOrderingRatio": _ratio(safe_flow, layout_total),
            },
            "conversion": {
                "total": conversion_total,
                "includedOrWitnessed": conversion_included,
                "coverage": conversion_coverage,
                "unresolved": unresolved_count,
                "unresolvedRatio": _ratio(unresolved_count, conversion_total),
                "diagnosticOnly": diagnostic_only_count,
                "outcomeCounts": conversion_outcomes,
            },
        },
        "violations": violations,
        "warnings": warnings,
    }
