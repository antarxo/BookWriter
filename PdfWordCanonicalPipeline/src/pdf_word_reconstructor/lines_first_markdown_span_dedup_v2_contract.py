from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .build_contract import build_build_contract
from .lines_first_markdown_span_contract import build_lines_first_markdown_span_contract
from .lines_first_markdown_augmented_contract import _norm, _row_text

VERSION = "lines-first-markdown-span-dedup-contract-0.2"
_MAX_LOOKAHEAD = 3
_MIN_FRAGMENT_CHARS = 18
_MIN_CONTAINMENT = 0.92
_MIN_CONTIGUOUS_RATIO = 0.78


def _page(row: dict[str, Any]) -> int:
    return int((row.get("layout") or {}).get("page") or 0)


def _tokens(text: str) -> list[str]:
    return [token for token in _norm(text).split() if token]


def _containment(fragment: str, whole: str) -> float:
    f = _tokens(fragment)
    w = _tokens(whole)
    if not f or not w:
        return 0.0
    wset = set(w)
    return sum(1 for token in f if token in wset) / max(1, len(f))


def _ordered_subsequence(fragment: str, whole: str) -> bool:
    f = _tokens(fragment)
    w = _tokens(whole)
    if not f or not w:
        return False
    pos = 0
    for token in f:
        try:
            pos = w.index(token, pos) + 1
        except ValueError:
            return False
    return True


def _longest_contiguous_run(fragment: str, whole: str) -> tuple[int, int, float]:
    f = _tokens(fragment)
    w = _tokens(whole)
    if not f or not w:
        return 0, 0, 0.0
    best_len = 0
    best_start = -1
    for wi in range(len(w)):
        run = 0
        while run < len(f) and wi + run < len(w) and f[run] == w[wi + run]:
            run += 1
        if run > best_len:
            best_len = run
            best_start = wi
    return best_len, best_start, best_len / max(1, len(f))


def _is_residual_duplicate(candidate: dict[str, Any], matched_row: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    fragment = _row_text(candidate).strip()
    whole = _row_text(matched_row).strip()
    fragment_norm = _norm(fragment)
    whole_norm = _norm(whole)
    if len(fragment_norm) < _MIN_FRAGMENT_CHARS:
        return False, {"reason": "fragment-too-short"}
    if _page(candidate) != _page(matched_row):
        return False, {"reason": "different-page"}

    containment = _containment(fragment, whole)
    subsequence = _ordered_subsequence(fragment, whole)
    direct = bool(fragment_norm and fragment_norm in whole_norm)
    run_len, run_start, run_ratio = _longest_contiguous_run(fragment, whole)

    # Residual fragments are expected to repeat a contiguous slice of the MMD block.
    # Keep the global containment guard, but allow a strong contiguous run to prove
    # duplication even when OCR/normalization breaks exact substring matching.
    accepted = containment >= _MIN_CONTAINMENT and (
        direct or subsequence or run_ratio >= _MIN_CONTIGUOUS_RATIO
    )
    return accepted, {
        "containment": round(containment, 4),
        "orderedSubsequence": subsequence,
        "directSubstring": direct,
        "contiguousRunTokens": run_len,
        "contiguousRunStart": run_start,
        "contiguousRunRatio": round(run_ratio, 4),
    }


def build_lines_first_markdown_span_dedup_v2_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    result = deepcopy(build_lines_first_markdown_span_contract(
        Path(lines_path), Path(mmd_path), page_width_pt=page_width_pt
    ))
    spine = result["pageLayoutSpine"]
    rows = list(spine.get("rows", []) or [])

    suppressed_indices: set[int] = set()
    suppression_records: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        match = row.get("linesFirstMarkdownSpanMatch")
        if not isinstance(match, dict) or not match.get("accepted"):
            continue
        page = _page(row)
        for j in range(i + 1, min(len(rows), i + 1 + _MAX_LOOKAHEAD)):
            if j in suppressed_indices:
                continue
            candidate = rows[j]
            if _page(candidate) != page:
                break
            other_match = candidate.get("linesFirstMarkdownSpanMatch")
            if isinstance(other_match, dict) and other_match.get("accepted"):
                break
            accepted, evidence = _is_residual_duplicate(candidate, row)
            if not accepted:
                break
            suppressed_indices.add(j)
            suppression_records.append({
                "keptRowIndex": i,
                "suppressedRowIndex": j,
                "page": page,
                "keptMarkdownId": row.get("markdownId"),
                "suppressedSlotId": (candidate.get("layout") or {}).get("slotId"),
                "suppressedText": _row_text(candidate),
                **evidence,
            })

    new_rows = [deepcopy(row) for idx, row in enumerate(rows) if idx not in suppressed_indices]
    for order, row in enumerate(new_rows):
        row["markdownOrder"] = order
        layout = row.get("layout") or {}
        layout["wordFlowOrder"] = order
        row["layout"] = layout

    suppressed_slots_by_page: dict[int, set[str]] = {}
    for rec in suppression_records:
        slot = str(rec.get("suppressedSlotId") or "")
        if slot:
            suppressed_slots_by_page.setdefault(int(rec["page"]), set()).add(slot)

    for page in result["pageStructure"].get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        slots = suppressed_slots_by_page.get(page_no, set())
        if slots:
            page["flow"] = [
                item for item in (page.get("flow", []) or [])
                if str(item.get("id") or "") not in slots
            ]

    spine["rows"] = new_rows
    spine["version"] = VERSION
    spine["policy"] = (
        "LINES_FIRST_MMD_SPAN_DEDUP_V2 preserves Lines-first span output and suppresses only immediately following same-page residual fragments. "
        "Suppression still requires >=92% token containment; exact substring/order evidence may be replaced by a >=78% contiguous token run to tolerate OCR normalization noise."
    )
    spine["residualDuplicateSuppression"] = {
        "enabled": True,
        "maxLookahead": _MAX_LOOKAHEAD,
        "minimumContainment": _MIN_CONTAINMENT,
        "minimumContiguousRunRatio": _MIN_CONTIGUOUS_RATIO,
        "minimumFragmentChars": _MIN_FRAGMENT_CHARS,
        "suppressed": suppression_records,
    }

    build_contract = build_build_contract(spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines-first+mmd-span-augmentation",
        "layout": "mathpix-lines",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }
    result["buildContract"] = build_contract
    result["version"] = VERSION

    summary = result.get("summary") or {}
    summary.update({
        "preDedupOutputUnitCount": len(rows),
        "outputUnitCount": len(new_rows),
        "residualDuplicateSuppressedCount": len(suppression_records),
        "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
        "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
    })
    result["summary"] = summary
    return result


__all__ = ["build_lines_first_markdown_span_dedup_v2_contract"]
