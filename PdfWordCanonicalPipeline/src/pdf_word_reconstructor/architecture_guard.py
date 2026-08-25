from __future__ import annotations

from typing import Any


def _summary(report: dict[str, Any] | None) -> dict[str, Any]:
    return (report or {}).get("summary", {}) or {}


def _has_unqualified_order_keys(page_layout_spine: dict[str, Any] | None) -> bool:
    order_map = (page_layout_spine or {}).get("layoutOrderBySlot") or {}
    return any(":" not in str(key) for key in order_map.keys())


SAFE_BUILDER_LAYOUT_POLICIES = {
    "safe-flow-order-from-layout-contract",
    "word-free-flow-single-section",
}


def build_architecture_guard(
    *,
    markdown_element_map: dict[str, Any] | None,
    markdown_pdf_spine: dict[str, Any] | None,
    docx_donor_map: dict[str, Any] | None,
    page_layout_spine: dict[str, Any] | None,
    conversion_spine: dict[str, Any] | None,
    fidelity_report: dict[str, Any] | None,
    build_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Report whether the run follows the Markdown/PDF/DOCX maps-first contract.

    This is deliberately separate from quality scoring. It answers one question:
    did the pipeline use the agreed architecture, or did an older fallback become
    an authority again?
    """
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []

    def violation(code: str, message: str, **data: Any) -> None:
        violations.append({"code": code, "message": message, **data})

    def warning(code: str, message: str, **data: Any) -> None:
        warnings.append({"code": code, "message": message, **data})

    def ok(code: str, message: str, **data: Any) -> None:
        confirmations.append({"code": code, "message": message, **data})

    markdown_records = len((markdown_element_map or {}).get("records", []) or [])
    if markdown_records:
        ok("markdown-map-present", "Mathpix Markdown element map is present.", records=markdown_records)
        docx_seed_count = sum(1 for record in (markdown_element_map or {}).get("records", []) or [] if record.get("docxEvidence"))
        if docx_seed_count:
            warning(
                "docx-evidence-seeded-before-donor-map",
                "Some Markdown records already contain DOCX evidence from the input map stage. This is allowed only as donor-map seed evidence, not as a direct production bypass.",
                recordsWithDocxEvidence=docx_seed_count,
            )
    else:
        warning("markdown-map-missing", "No Markdown element map is present; the run cannot be fully markdown-first.")

    if markdown_records and not markdown_pdf_spine:
        violation("markdown-pdf-spine-missing", "Markdown records exist but markdown_pdf_spine is missing.")
    elif markdown_pdf_spine:
        ok(
            "markdown-pdf-spine-present",
            "Markdown/PDF spine is present.",
            coverage=(markdown_pdf_spine or {}).get("coverage"),
            items=(markdown_pdf_spine or {}).get("itemCount"),
        )

    donor_summary = _summary(docx_donor_map)
    if not docx_donor_map:
        violation("docx-donor-map-missing", "DOCX donor map is missing; DOCX evidence would require ad-hoc scanning.")
    else:
        ok(
            "docx-donor-map-present",
            "DOCX donor map is present.",
            ommlCandidates=donor_summary.get("mathCandidateCount"),
            markdownLinks=donor_summary.get("markdownLinkedParagraphCount"),
            pdfLinks=donor_summary.get("pdfLinkedParagraphCount"),
        )

    layout_summary = _summary(page_layout_spine)
    if markdown_pdf_spine and not page_layout_spine:
        violation("page-layout-spine-missing", "Markdown/PDF spine exists but page_layout_spine is missing.")
    elif page_layout_spine:
        layout_preflight = (page_layout_spine or {}).get("layoutPreflight") or {}
        page_setup_estimate = layout_preflight.get("pageSetupEstimate") or {}
        ok(
            "page-layout-spine-present",
            "Page layout spine is present.",
            coverage=layout_summary.get("coverage"),
            contractCoverage=layout_summary.get("contractCoverage"),
            safeFlowOrderingSlots=layout_summary.get("safeFlowOrderingSlotCount"),
        )
        if _has_unqualified_order_keys(page_layout_spine):
            violation("unqualified-layout-order-keys", "layoutOrderBySlot contains non page-qualified keys.")
        if not page_setup_estimate:
            violation(
                "layout-preflight-missing",
                "page_layout_spine must contain layoutPreflight.pageSetupEstimate before Word build.",
            )
        else:
            ok(
                "layout-preflight-present",
                "Page setup, mirror margins, columns and local typography policy are pre-estimated in the map.",
                pageSetupEstimate=page_setup_estimate,
                columnProfile=layout_preflight.get("columnProfile"),
                localTypographyPolicy=layout_preflight.get("localTypographyPolicy"),
            )
        safe_count = int(layout_summary.get("safeFlowOrderingSlotCount", 0) or 0)
        order_count = len((page_layout_spine or {}).get("layoutOrderBySlot") or {})
        if order_count > safe_count:
            violation(
                "unsafe-layout-order-map",
                "layoutOrderBySlot contains more entries than the safe flow-ordering contract allows.",
                orderMapCount=order_count,
                safeFlowOrderingSlotCount=safe_count,
            )

    build_layout = (build_report or {}).get("layout_spine") or {}
    word_section_policy = (build_report or {}).get("word_section_policy") or {}
    if page_layout_spine:
        policy = str(build_layout.get("policy") or "")
        if policy not in SAFE_BUILDER_LAYOUT_POLICIES:
            violation(
                "builder-layout-policy-not-safe-contract",
                "Builder did not report an approved layout-spine Word-flow policy while page_layout_spine exists.",
                policy=policy,
                approvedPolicies=sorted(SAFE_BUILDER_LAYOUT_POLICIES),
            )
        else:
            ok("builder-uses-safe-layout-contract", "Builder reports approved layout-spine Word-flow policy.", policy=policy)
        section_policy = str((build_report or {}).get("section_break_policy") or word_section_policy.get("policy") or "")
        if section_policy != "word-free-flow-single-section":
            violation(
                "builder-not-word-free-flow",
                "Builder must use one natural Word-flow section, not one hard section/page boundary per PDF page.",
                sectionBreakPolicy=section_policy,
            )
        elif bool(word_section_policy.get("hardBreaksBetweenPdfPages")) or bool(word_section_policy.get("sectionPerPdfPage")):
            violation(
                "builder-reports-hard-pdf-page-breaks",
                "Builder reports hard page/section boundaries between PDF pages.",
                wordSectionPolicy=word_section_policy,
            )
        else:
            margin_source = str(word_section_policy.get("marginsSource") or "")
            if margin_source not in {"pdf-main-flow-mirror-margins", "pdf-main-flow-margins"}:
                violation(
                    "builder-margin-source-not-layout-preflight",
                    "Builder margins must come from page_layout_spine.layoutPreflight.pageSetupEstimate.",
                    marginsSource=margin_source,
                    allowedSources=["pdf-main-flow-mirror-margins", "pdf-main-flow-margins"],
                )
            else:
                ok(
                    "builder-uses-layout-preflight-margins",
                    "Builder margins come from the maps-first page setup estimate.",
                    marginsSource=margin_source,
                )
            ok(
                "builder-uses-natural-word-pagination",
                "Builder uses one free-flow Word section and lets Word paginate after typography changes.",
                intendedSectionCount=word_section_policy.get("intendedSectionCount"),
                marginsSource=word_section_policy.get("marginsSource"),
            )

    conversion_summary = _summary(conversion_spine)
    if markdown_records and not conversion_spine:
        violation("conversion-spine-missing", "Markdown records exist but conversion_spine is missing.")
    elif conversion_spine:
        ok(
            "conversion-spine-present",
            "Conversion spine is present and should be the primary user-decision authority.",
            decisions=conversion_summary.get("decisionRequiredCount"),
            coverage=conversion_summary.get("coverage"),
        )

    fidelity_summary = _summary(fidelity_report)
    legacy_decisions = int(fidelity_summary.get("userDecisionQueueCount", 0) or 0)
    conversion_decisions = int(conversion_summary.get("decisionRequiredCount", 0) or 0)
    if conversion_spine and legacy_decisions and legacy_decisions != conversion_decisions:
        warning(
            "legacy-fidelity-decisions-differ",
            "Legacy fidelity decision count differs from conversion spine; UI/status must prefer conversion spine.",
            legacyDecisions=legacy_decisions,
            conversionDecisions=conversion_decisions,
        )
    if fidelity_report:
        ok(
            "fidelity-report-diagnostic-only",
            "fidelity_fallback_report is present; in maps-first runs it must remain diagnostic/summary support.",
            userDecisionQueueCount=legacy_decisions,
            actionableReviewQueueCount=fidelity_summary.get("actionableReviewQueueCount"),
            diagnosticReviewQueueCount=fidelity_summary.get("diagnosticReviewQueueCount"),
        )

    status = "pass" if not violations else "fail"
    return {
        "version": "architecture-guard-0.3",
        "truthModel": "markdown-first/pdf-guided/docx-secondary/output-audited",
        "status": status,
        "violationCount": len(violations),
        "warningCount": len(warnings),
        "confirmations": confirmations,
        "warnings": warnings,
        "violations": violations,
    }
