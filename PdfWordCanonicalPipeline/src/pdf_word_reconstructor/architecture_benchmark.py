from __future__ import annotations

from collections import Counter
from typing import Any


def _build_items(build_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in (build_report or {}).get("pages", []) or []:
        for item in page.get("items", []) or []:
            row = dict(item)
            row["page"] = page.get("page")
            items.append(row)
    return items


def build_architecture_benchmark(
    *,
    pdf_path: str,
    docx_path: str,
    pages: list[int],
    total_seconds: float,
    docx_donor_map: dict[str, Any] | None,
    conversion_spine: dict[str, Any] | None,
    page_layout_spine: dict[str, Any] | None,
    fidelity_report: dict[str, Any] | None,
    content_audit: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
) -> dict[str, Any]:
    fidelity_summary = (fidelity_report or {}).get("summary", {}) or {}
    donor_summary = (docx_donor_map or {}).get("summary", {}) or {}
    spine_summary = (conversion_spine or {}).get("summary", {}) or {}
    layout_summary = (page_layout_spine or {}).get("summary", {}) or {}
    survival = (content_audit or {}).get("markdown_survival", {}) or {}
    items = _build_items(build_report)
    source_counts = Counter(str(item.get("source") or "") for item in items)
    docx_omml_used = 0
    for row in (conversion_spine or {}).get("rows", []) or []:
        donor = row.get("docxDonor") or {}
        if donor.get("usedInOutput") and int(donor.get("ommlCount") or 0) > 0:
            docx_omml_used += 1

    return {
        "version": "architecture-benchmark-0.1",
        "architecture": "maps-first-v0.1",
        "source": {
            "pdf": pdf_path,
            "docx": docx_path,
            "pages": pages,
            "pageRange": f"{min(pages)}-{max(pages)}" if pages else "",
        },
        "timing": {
            "totalSeconds": round(float(total_seconds), 3),
        },
        "quality": {
            "nativeWordMath": int((fidelity_summary.get("finalEquationStatusCounts") or {}).get("native-word-math", 0) or 0),
            "visualEquationFallbacks": int(
                fidelity_summary.get("rasterEquationFallbacks")
                or (fidelity_summary.get("finalEquationStatusCounts") or {}).get("visual-fallback-latex-conversion-failed", 0)
                or 0
            ),
            "markdownSurvivalCoverage": survival.get("coverage", fidelity_summary.get("markdownSurvivalCoverage")),
            "markdownSurvivalMissing": survival.get("missing_count", fidelity_summary.get("markdownSurvivalMissing")),
            "userDecisions": int(spine_summary.get("decisionRequiredCount", fidelity_summary.get("userDecisionQueueCount", 0)) or 0),
            "actionableReview": int(fidelity_summary.get("actionableReviewQueueCount", 0) or 0),
        },
        "maps": {
            "docxOmmlCandidates": int(donor_summary.get("mathCandidateCount", 0) or 0),
            "docxOmmlUsed": int(docx_omml_used or source_counts.get("docx-native-omml", 0) or 0),
            "docxMarkdownLinkedParagraphs": int(donor_summary.get("markdownLinkedParagraphCount", 0) or 0),
            "docxPdfLinkedParagraphs": int(donor_summary.get("pdfLinkedParagraphCount", 0) or 0),
            "conversionSpineRows": int(spine_summary.get("selectedRowCount", 0) or 0),
            "conversionSpineCoverage": spine_summary.get("coverage"),
            "conversionSpineDecisionRows": int(spine_summary.get("decisionRequiredCount", 0) or 0),
            "pageLayoutSpineRows": int(layout_summary.get("rowCount", 0) or 0),
            "pageLayoutSpineCoverage": layout_summary.get("coverage"),
            "pageLayoutContractCoverage": layout_summary.get("contractCoverage"),
            "pageLayoutContractUsable": int(layout_summary.get("contractUsableCount", 0) or 0),
            "pageLayoutSafeFlowOrderingSlots": int(layout_summary.get("safeFlowOrderingSlotCount", 0) or 0),
            "pageLayoutSpineUnplaced": int(layout_summary.get("unplacedLayoutSlotCount", 0) or 0),
        },
        "interpretation": (
            "Baseline for maps-first architecture. Compare future runs on the same PDF/page range "
            "using totalSeconds, nativeWordMath, visualEquationFallbacks, userDecisions, "
            "docxOmmlCandidates/docxOmmlUsed, and markdownSurvivalCoverage."
        ),
    }
