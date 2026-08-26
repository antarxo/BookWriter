from __future__ import annotations

from collections import defaultdict
import re
import unicodedata
from typing import Any

from rapidfuzz import fuzz


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _item_page(item: dict[str, Any]) -> int:
    for key in ("pdfPage", "inferredPage", "markdownPageHint"):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def _pdf_region_lookup(pdf_analysis: dict[str, Any] | None) -> dict[tuple[int, str], dict[str, Any]]:
    lookup: dict[tuple[int, str], dict[str, Any]] = {}
    for page in (pdf_analysis or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for region in page.get("regions", []) or []:
            region_id = str(region.get("id") or "")
            if region_id:
                lookup[(page_no, region_id)] = region
    return lookup


def _group_audit_record(
    page_no: int,
    group: dict[str, Any],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any]:
    members: list[dict[str, Any]] = []
    for member_id in group.get("member_ids", []) or []:
        region = region_lookup.get((page_no, str(member_id))) or {}
        semantic = region.get("semantic") if isinstance(region.get("semantic"), dict) else {}
        members.append({
            "id": member_id,
            "bbox": region.get("bbox"),
            "text": str(region.get("text") or "")[:260],
            "semanticType": semantic.get("type"),
            "semanticConfidence": semantic.get("confidence"),
            "semanticReasons": list(semantic.get("reasons") or []),
        })
    return {
        "id": group.get("id"),
        "bbox": group.get("bbox"),
        "memberCount": len(group.get("member_ids") or []),
        "memberIds": list(group.get("member_ids") or []),
        "members": members,
    }


def _item_equation_text(item: dict[str, Any]) -> str:
    authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
    return str(
        authoritative.get("latex")
        or item.get("latex")
        or authoritative.get("rawMarkdown")
        or item.get("rawMarkdown")
        or authoritative.get("plainText")
        or item.get("text")
        or ""
    )


def _group_equation_text(page_no: int, group: dict[str, Any], region_lookup: dict[tuple[int, str], dict[str, Any]]) -> str:
    parts: list[str] = []
    direct = str(group.get("text") or "").strip()
    if direct:
        parts.append(direct)
    for member_id in group.get("member_ids", []) or []:
        region = region_lookup.get((page_no, str(member_id))) or {}
        text = str(region.get("text") or "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


_LATEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "varepsilon": "ε", "theta": "θ", "vartheta": "θ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "omega": "ω",
    "Gamma": "Γ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π", "Sigma": "Σ", "Omega": "Ω",
    "prime": "'", "cdot": "", "times": "", "left": "", "right": "",
    "mathrm": "", "mathbf": "", "boldsymbol": "", "text": "", "operatorname": "",
    "displaystyle": "", "quad": "", "qquad": "",
}


def _flatten_frac(text: str) -> str:
    pattern = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
    previous = None
    while text != previous:
        previous = text
        text = pattern.sub(r"\1/\2", text)
    return text


def _equation_signature(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _flatten_frac(text)
    for name, replacement in _LATEX_SYMBOLS.items():
        text = re.sub(rf"\\{re.escape(name)}\b", replacement, text)
    text = re.sub(r"\\begin\{[^{}]+\}|\\end\{[^{}]+\}", "", text)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = text.replace("·", "").replace("⋅", "").replace("×", "")
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"\^\s*\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"_\s*\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"[{}$\\\s]", "", text)
    text = re.sub(r"[^0-9A-Za-zΑ-Ωα-ωά-ώ+\-=/().,'']", "", text)
    return text.casefold()


def _equation_score(a: str, b: str) -> float:
    sa = _equation_signature(a)
    sb = _equation_signature(b)
    if not sa or not sb:
        return 0.0
    if sa == sb:
        return 100.0
    shorter, longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(shorter) >= 5 and shorter in longer:
        coverage = len(shorter) / max(1, len(longer))
        return min(99.0, 86.0 + 13.0 * coverage)
    return max(float(fuzz.ratio(sa, sb)), float(fuzz.partial_ratio(sa, sb)) * 0.96)


def _attach_group(item: dict[str, Any], group: dict[str, Any], page_no: int, *, match_mode: str, score: float | None) -> None:
    box = _bbox(group.get("bbox"))
    group_id = str(group.get("id") or "")
    item["pdfPage"] = page_no
    item["pdfRegion"] = group_id
    item["pdfParentRegion"] = None
    item["pdfLineIndex"] = None
    item["pdfRowGranularity"] = "pdf-equation-group"
    item["bbox"] = box
    item["status"] = "equation-group"
    item["manifestOutcome"] = "pdf-equation-group-witness-confirmed"
    item["matchMode"] = match_mode
    item["score"] = round(float(score), 2) if score is not None else None
    geometry = item.get("pdfGeometry") if isinstance(item.get("pdfGeometry"), dict) else {}
    geometry["bbox"] = box
    geometry["regionBBox"] = box
    geometry["page"] = page_no
    item["pdfGeometry"] = geometry
    item["pdfEquationGroup"] = {
        "id": group_id,
        "memberIds": list(group.get("member_ids") or []),
        "memberKinds": list(group.get("member_kinds") or []),
        "source": "page_structure.visual_groups",
    }


def _high_confidence_mismatch_matches(
    page_no: int,
    items: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    region_lookup: dict[tuple[int, str], dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], float, float, float]]:
    if not items or not groups:
        return []
    item_texts = [_item_equation_text(item) for item in items]
    group_texts = [_group_equation_text(page_no, group, region_lookup) for group in groups]
    matrix = [[_equation_score(item_text, group_text) for group_text in group_texts] for item_text in item_texts]

    proposals: list[tuple[float, int, int, float, float]] = []
    for i, row in enumerate(matrix):
        ranked = sorted(((score, j) for j, score in enumerate(row)), reverse=True)
        if not ranked:
            continue
        best_score, best_j = ranked[0]
        second_score = ranked[1][0] if len(ranked) > 1 else 0.0
        column = sorted(((matrix[ii][best_j], ii) for ii in range(len(items))), reverse=True)
        reciprocal_score, reciprocal_i = column[0]
        reciprocal_second = column[1][0] if len(column) > 1 else 0.0
        if reciprocal_i != i:
            continue
        item_margin = best_score - second_score
        group_margin = reciprocal_score - reciprocal_second
        accepted = (
            best_score >= 92.0
            and item_margin >= 10.0
            and group_margin >= 8.0
        ) or (
            best_score >= 97.0
            and item_margin >= 6.0
            and group_margin >= 6.0
        )
        if accepted:
            proposals.append((best_score, i, best_j, item_margin, group_margin))

    proposals.sort(reverse=True)
    used_items: set[int] = set()
    used_groups: set[int] = set()
    matches: list[tuple[dict[str, Any], dict[str, Any], float, float, float]] = []
    for best_score, i, j, item_margin, group_margin in proposals:
        if i in used_items or j in used_groups:
            continue
        used_items.add(i)
        used_groups.add(j)
        matches.append((items[i], groups[j], best_score, item_margin, group_margin))
    return matches


def bind_display_equations_to_pdf_groups(
    markdown_pdf_spine: dict[str, Any],
    page_structure: dict[str, Any],
    pdf_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind unplaced Markdown display equations to clustered PDF equation groups.

    Equal-count pages retain strict vertical-order binding. Count-mismatch pages
    may bind only reciprocal, high-confidence text matches with a clear margin;
    raw PDF equation fragments are never bound directly.
    """
    items_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in markdown_pdf_spine.get("items", []) or []:
        if str(item.get("type") or "") != "display_equation":
            continue
        if item.get("pdfRegion") and _bbox(item.get("bbox")):
            continue
        page_no = _item_page(item)
        if page_no:
            items_by_page[page_no].append(item)

    groups_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for group in page.get("visual_groups", []) or []:
            if str(group.get("kind") or "") != "equation":
                continue
            if not _bbox(group.get("bbox")):
                continue
            groups_by_page[page_no].append(group)

    region_lookup = _pdf_region_lookup(pdf_analysis)
    bound = 0
    mismatch_text_bound = 0
    pages: list[dict[str, Any]] = []
    used_group_ids: set[str] = set()
    for page_no in sorted(set(items_by_page) | set(groups_by_page)):
        items = sorted(items_by_page.get(page_no, []), key=lambda row: int(row.get("orderIndex") or 0))
        groups = [group for group in groups_by_page.get(page_no, []) if str(group.get("id") or "") not in used_group_ids]
        groups.sort(key=lambda group: ((_bbox(group.get("bbox")) or [0, 0, 0, 0])[1], (_bbox(group.get("bbox")) or [0, 0, 0, 0])[0]))
        page_record = {
            "page": page_no,
            "unplacedMarkdownDisplayEquationCount": len(items),
            "pdfEquationGroupCount": len(groups),
            "groupDelta": len(groups) - len(items),
            "groupMemberCounts": [len(group.get("member_ids") or []) for group in groups],
            "groups": [_group_audit_record(page_no, group, region_lookup) for group in groups],
            "markdownItems": [
                {
                    "id": item.get("id"),
                    "orderIndex": item.get("orderIndex"),
                    "text": _item_equation_text(item)[:320],
                }
                for item in items
            ],
            "boundCount": 0,
            "mismatchTextMatches": [],
            "policy": None,
        }
        if not items:
            page_record["policy"] = "no-unplaced-markdown-equations"
            pages.append(page_record)
            continue

        if len(items) == len(groups):
            for item, group in zip(items, groups):
                _attach_group(item, group, page_no, match_mode="page-structure-equation-group-order", score=None)
                group_id = str(group.get("id") or "")
                used_group_ids.add(group_id)
                bound += 1
                page_record["boundCount"] += 1
            page_record["policy"] = "bound-by-equal-count-and-vertical-order"
            pages.append(page_record)
            continue

        matches = _high_confidence_mismatch_matches(page_no, items, groups, region_lookup)
        for item, group, score, item_margin, group_margin in matches:
            _attach_group(
                item,
                group,
                page_no,
                match_mode="page-structure-equation-group-unique-text-mismatch",
                score=score,
            )
            group_id = str(group.get("id") or "")
            used_group_ids.add(group_id)
            bound += 1
            mismatch_text_bound += 1
            page_record["boundCount"] += 1
            page_record["mismatchTextMatches"].append({
                "markdownId": item.get("id"),
                "groupId": group_id,
                "score": round(float(score), 2),
                "itemMargin": round(float(item_margin), 2),
                "groupMargin": round(float(group_margin), 2),
            })
        page_record["policy"] = (
            "partial-bind-reciprocal-high-confidence-text-under-count-mismatch"
            if matches else "no-bind-count-mismatch-no-unique-high-confidence-text"
        )
        pages.append(page_record)

    equation_pages = [row for row in pages if int(row.get("unplacedMarkdownDisplayEquationCount") or 0) > 0]
    mismatch_pages = [
        row for row in equation_pages
        if int(row.get("unplacedMarkdownDisplayEquationCount") or 0) != int(row.get("pdfEquationGroupCount") or 0)
    ]
    markdown_equation_count = sum(int(row.get("unplacedMarkdownDisplayEquationCount") or 0) for row in equation_pages)
    pdf_group_count = sum(int(row.get("pdfEquationGroupCount") or 0) for row in equation_pages)
    extra_group_count = sum(max(0, int(row.get("groupDelta") or 0)) for row in equation_pages)
    missing_group_count = sum(max(0, -int(row.get("groupDelta") or 0)) for row in equation_pages)

    audit = {
        "version": "donorless-equation-group-binding-0.4",
        "source": "page_structure.visual_groups[kind=equation]",
        "boundCount": bound,
        "policy": "never-bind-raw-pdf-equation-fragments; equal counts use order; count mismatches allow only reciprocal high-confidence text with clear margins",
        "summary": {
            "equationPageCount": len(equation_pages),
            "mismatchPageCount": len(mismatch_pages),
            "mismatchPageRate": round(len(mismatch_pages) / len(equation_pages), 5) if equation_pages else 0.0,
            "markdownDisplayEquationCount": markdown_equation_count,
            "pdfEquationGroupCount": pdf_group_count,
            "boundDisplayEquationCount": bound,
            "mismatchTextBoundCount": mismatch_text_bound,
            "bindingCoverage": round(bound / markdown_equation_count, 5) if markdown_equation_count else 1.0,
            "extraPdfEquationGroupCount": extra_group_count,
            "missingPdfEquationGroupCount": missing_group_count,
            "extraGroupPerMarkdownEquation": round(extra_group_count / markdown_equation_count, 5) if markdown_equation_count else 0.0,
        },
        "pages": pages,
    }
    markdown_pdf_spine["displayEquationGroupBinding"] = audit
    return audit
