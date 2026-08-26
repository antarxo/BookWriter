from __future__ import annotations

from collections import Counter
from typing import Any


VERSION = "donorless-benchmark-report-0.1"


def build_benchmark_report(
    *,
    build_contract: dict[str, Any],
    equation_group_binding: dict[str, Any] | None,
    selected_pages: list[int] | None,
) -> dict[str, Any]:
    summary = build_contract.get("summary") or {}
    items = list(build_contract.get("items") or [])

    unresolved_items = [item for item in items if str(item.get("status") or "") == "unresolved"]
    unresolved_by_markdown_type: Counter[str] = Counter()
    unresolved_by_output_kind: Counter[str] = Counter()
    unresolved_by_page: Counter[int] = Counter()
    unresolved_by_reason: Counter[str] = Counter()
    non_equation_samples: list[dict[str, Any]] = []

    for item in unresolved_items:
        markdown_type = str(item.get("markdownType") or "unknown")
        output_kind = str(item.get("outputKind") or "unknown")
        page = int(((item.get("placement") or {}).get("page")) or 0)
        unresolved_by_markdown_type[markdown_type] += 1
        unresolved_by_output_kind[output_kind] += 1
        if page:
            unresolved_by_page[page] += 1
        for reason in item.get("unresolved") or []:
            unresolved_by_reason[str(reason)] += 1
        if output_kind != "equation" and len(non_equation_samples) < 40:
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            text = str(
                content.get("text")
                or content.get("plainText")
                or content.get("latex")
                or item.get("rawMarkdown")
                or ""
            )
            placement = item.get("placement") or {}
            non_equation_samples.append({
                "markdownId": item.get("markdownId"),
                "markdownType": item.get("markdownType"),
                "outputKind": output_kind,
                "page": page or None,
                "slotId": placement.get("slotId"),
                "reasons": list(item.get("unresolved") or []),
                "textPreview": text[:220],
            })

    eq = equation_group_binding or {}
    eq_metrics = eq.get("metrics") if isinstance(eq.get("metrics"), dict) else {}
    eq_pages = [
        row for row in (eq.get("pages") or [])
        if int(row.get("unplacedMarkdownDisplayEquationCount") or 0) > 0
    ]
    if not eq_metrics:
        total_md = sum(int(row.get("unplacedMarkdownDisplayEquationCount") or 0) for row in eq_pages)
        total_bound = sum(int(row.get("boundCount") or 0) for row in eq_pages)
        mismatch_pages = sum(
            1 for row in eq_pages
            if int(row.get("unplacedMarkdownDisplayEquationCount") or 0) != int(row.get("pdfEquationGroupCount") or 0)
        )
        total_groups = sum(int(row.get("pdfEquationGroupCount") or 0) for row in eq_pages)
        extra = sum(
            max(0, int(row.get("pdfEquationGroupCount") or 0) - int(row.get("unplacedMarkdownDisplayEquationCount") or 0))
            for row in eq_pages
        )
        missing = sum(
            max(0, int(row.get("unplacedMarkdownDisplayEquationCount") or 0) - int(row.get("pdfEquationGroupCount") or 0))
            for row in eq_pages
        )
        eq_metrics = {
            "markdownDisplayEquationCount": total_md,
            "boundDisplayEquationCount": total_bound,
            "bindingCoverage": round(total_bound / total_md, 5) if total_md else 1.0,
            "equationPageCount": len(eq_pages),
            "mismatchPageCount": mismatch_pages,
            "mismatchPageRate": round(mismatch_pages / len(eq_pages), 5) if eq_pages else 0.0,
            "pdfEquationGroupCount": total_groups,
            "extraPdfEquationGroupCount": extra,
            "missingPdfEquationGroupCount": missing,
            "extraGroupPerMarkdownEquation": round(extra / total_md, 5) if total_md else 0.0,
        }

    item_count = int(summary.get("itemCount") or len(items))
    ready_count = int(summary.get("readyCount") or 0)
    unresolved_count = int(summary.get("unresolvedCount") or len(unresolved_items))
    pages = list(selected_pages or [])

    return {
        "version": VERSION,
        "scope": {
            "selectedPageCount": len(pages),
            "firstPage": min(pages) if pages else None,
            "lastPage": max(pages) if pages else None,
        },
        "overall": {
            "itemCount": item_count,
            "readyCount": ready_count,
            "unresolvedCount": unresolved_count,
            "readyCoverage": round(ready_count / item_count, 5) if item_count else 1.0,
            "unresolvedRate": round(unresolved_count / item_count, 5) if item_count else 0.0,
        },
        "unresolved": {
            "byOutputKind": dict(sorted(unresolved_by_output_kind.items())),
            "byMarkdownType": dict(sorted(unresolved_by_markdown_type.items())),
            "byReason": dict(sorted(unresolved_by_reason.items())),
            "byPage": {str(key): value for key, value in sorted(unresolved_by_page.items())},
            "nonEquationCount": sum(value for key, value in unresolved_by_output_kind.items() if key != "equation"),
            "equationCount": int(unresolved_by_output_kind.get("equation", 0)),
            "nonEquationSamples": non_equation_samples,
        },
        "equations": eq_metrics,
        "sourceBuildContractVersion": build_contract.get("version"),
        "sourceEquationBindingVersion": eq.get("version"),
    }
