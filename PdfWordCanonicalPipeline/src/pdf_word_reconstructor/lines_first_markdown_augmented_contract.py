from __future__ import annotations

import re
from copy import deepcopy
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pdf_word_canonical_pipeline.markdown_element_map import extract_markdown_element_map

from .build_contract import build_build_contract
from .lines_only_region_contract import build_lines_only_region_contract

VERSION = "lines-first-markdown-augmented-contract-0.2"


def _clean_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\\(?:section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{([^{}]*)\}", r"\1", text)
    text = text.replace("\\\\", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _norm(value: str) -> str:
    text = _clean_text(value).casefold()
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|emph|textbf|textit)", " ", text)
    text = re.sub(r"[^0-9a-zα-ωά-ώ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    containment = 0.0
    if len(short) >= 12 and short in long:
        containment = 82.0 + 18.0 * len(short) / max(1, len(long))
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb)
    token = 0.0
    if overlap:
        coverage = overlap / max(1, min(len(ta), len(tb)))
        balance = (2 * overlap) / max(1, len(ta) + len(tb))
        token = 70.0 * coverage + 30.0 * balance
    seq = 100.0 * SequenceMatcher(None, na, nb).ratio()
    return max(containment, token, seq)


def _semantic(md_type: str) -> str:
    return {
        "heading": "heading",
        "title": "heading",
        "caption": "caption",
        "display_equation": "equation",
        "paragraph": "paragraph",
        "list": "list",
        "latex_list": "list",
    }.get(str(md_type or ""), "paragraph")


def _markdown_records(mmd_path: Path) -> list[dict[str, Any]]:
    mmd_path = Path(mmd_path)
    source = mmd_path.read_text(encoding="utf-8", errors="replace")
    with TemporaryDirectory(prefix="bookwriter-mdmap-") as td:
        mapped = extract_markdown_element_map([mmd_path], Path(td) / "markdown_element_map.json")
    records: list[dict[str, Any]] = []
    for raw in mapped.get("records", []) or []:
        kind = str(raw.get("type") or "")
        if kind not in {"heading", "title", "caption", "display_equation", "paragraph", "list", "latex_list"}:
            continue
        try:
            start, end = int(raw.get("offset") or 0), int(raw.get("endOffset") or 0)
        except (TypeError, ValueError):
            continue
        fragment = source[start:end].strip() if end > start else ""
        if kind in {"heading", "title", "caption"} and raw.get("text"):
            text = str(raw.get("text") or "").strip()
        elif kind == "display_equation" and raw.get("latex"):
            text = str(raw.get("latex") or "").strip()
        else:
            text = fragment
        text = _clean_text(text)
        if not text:
            continue
        records.append({
            "id": raw.get("id"),
            "order": int(raw.get("orderIndex") or len(records)),
            "page": raw.get("page"),
            "pageConfidence": raw.get("pageConfidence"),
            "type": kind,
            "semantic": _semantic(kind),
            "text": text,
            "raw": fragment,
        })
    return records


def _lines_rows(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    return list((artifacts.get("pageLayoutSpine") or {}).get("rows", []) or [])


def _row_text(row: dict[str, Any]) -> str:
    for value in (row.get("markdownText"), ((row.get("authoritativeContent") or {}).get("text"))):
        if value:
            return str(value)
    return ""


def _match_rows(rows: list[dict[str, Any]], md: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strict one-to-one monotonic Lines-first matching.

    Each Markdown element may be consumed at most once. A later Lines row may only
    match a later Markdown record. Page hints may boost a candidate but never allow
    backward or repeated binding. Lines remain the authoritative geometry skeleton.
    """
    matches: list[dict[str, Any]] = []
    cursor = 0
    used: set[int] = set()
    for index, row in enumerate(rows):
        line_text = _row_text(row)
        page = int((row.get("layout") or {}).get("page") or 0)
        stop = min(len(md), cursor + 24)
        candidates = [i for i in range(cursor, stop) if i not in used]
        if not candidates:
            matches.append({
                "rowIndex": index,
                "page": page,
                "linesText": line_text,
                "markdownIndex": None,
                "markdownId": None,
                "score": 0.0,
                "accepted": False,
            })
            continue

        best_i = None
        best_score = 0.0
        for i in candidates:
            item = md[i]
            score = _score(line_text, item["text"])
            if item.get("page") == page:
                score += 3.0
            distance = i - cursor
            score -= min(8.0, distance * 0.25)
            if score > best_score:
                best_i, best_score = i, score

        accepted = best_i is not None and best_score >= 62.0
        matches.append({
            "rowIndex": index,
            "page": page,
            "linesText": line_text,
            "markdownIndex": best_i if accepted else None,
            "markdownId": md[best_i]["id"] if accepted else None,
            "score": round(best_score, 3),
            "accepted": accepted,
        })
        if accepted and best_i is not None:
            used.add(best_i)
            cursor = best_i + 1
    return matches


def build_lines_first_markdown_augmented_contract(
    lines_path: Path,
    mmd_path: Path,
    page_width_pt: float = 595.276,
) -> dict[str, Any]:
    result = deepcopy(build_lines_only_region_contract(Path(lines_path), page_width_pt=page_width_pt))
    md = _markdown_records(Path(mmd_path))
    rows = _lines_rows(result)
    matches = _match_rows(rows, md)

    page_structure = result["pageStructure"]
    flow_by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for item in page.get("flow", []) or []:
            if item.get("type") == "text" and item.get("id"):
                flow_by_key[(page_no, str(item["id"]))] = item

    accepted = 0
    semantic_changes = 0
    text_changes = 0
    used_markdown_ids: list[str] = []
    for row, match in zip(rows, matches):
        if not match["accepted"]:
            continue
        item = md[int(match["markdownIndex"])]
        accepted += 1
        used_markdown_ids.append(str(item.get("id") or ""))
        old_text = _row_text(row)
        new_text = item["text"]
        old_sem = str(row.get("markdownType") or "paragraph")
        new_sem = str(item.get("semantic") or old_sem)
        if _norm(old_text) != _norm(new_text):
            text_changes += 1
        if new_sem != old_sem:
            semantic_changes += 1

        row["markdownId"] = f"mmd:{item.get('id')}"
        row["markdownType"] = new_sem
        row["markdownText"] = new_text
        row["rawMarkdown"] = item.get("raw") or ""
        row["authoritativeContent"] = {"text": new_text, "plainText": new_text, "source": "mathpix-mmd-augmentation"}
        row["linesFirstMarkdownMatch"] = match

        layout = row.get("layout") or {}
        page_no = int(layout.get("page") or 0)
        slot_id = str(layout.get("slotId") or "")
        flow_item = flow_by_key.get((page_no, slot_id))
        if flow_item is not None:
            flow_item["text"] = new_text
            flow_item["semantic_type"] = new_sem
            flow_item["content_source"] = "mathpix-mmd-via-lines-match"

        contract = row.get("layoutContract") or {}
        contract["authoritativeContent"] = row["authoritativeContent"]
        style = contract.get("styleHint") or {}
        style["role"] = "math" if new_sem == "equation" else new_sem
        style["semanticType"] = new_sem
        style["source"] = "mathpix-mmd-semantic-via-lines-match"
        contract["styleHint"] = style
        slot = contract.get("slot") or {}
        slot["semanticType"] = new_sem
        contract["slot"] = slot
        row["layoutContract"] = contract

    spine = result["pageLayoutSpine"]
    spine["version"] = VERSION
    spine["policy"] = (
        "LINES_FIRST_MMD keeps L2/L2.1 Lines geometry and grouped-unit skeleton. "
        "Mathpix MMD may augment only a strict one-to-one monotonic match; "
        "it may not create geometry or floating placement."
    )
    spine["linesFirstMarkdown"] = {
        "linesGeometryAuthority": True,
        "linesGroupingSkeleton": True,
        "markdownContentSemantics": True,
        "markdownMayCreateGeometry": False,
        "positionedFramesDisabled": True,
        "matchThreshold": 62.0,
        "matchingPolicy": "strict-one-to-one-monotonic-forward-only",
        "matches": matches,
    }

    build_contract = build_build_contract(spine)
    build_contract["sourceAuthority"] = {
        "content": "mathpix-lines-first+mmd-augmentation",
        "layout": "mathpix-lines",
        "typography": "mathpix-lines",
        "nativeDonor": None,
    }
    result["buildContract"] = build_contract
    result["version"] = VERSION
    result["markdownElementCount"] = len(md)
    result["markdownElements"] = md
    summary = result.get("summary") or {}
    summary.update({
        "markdownElementCount": len(md),
        "markdownMatchedUnitCount": accepted,
        "markdownUnmatchedUnitCount": len(rows) - accepted,
        "markdownSemanticChangeCount": semantic_changes,
        "markdownTextChangeCount": text_changes,
        "markdownMatchCoverage": round(accepted / len(rows), 5) if rows else 1.0,
        "markdownUniqueMatchedElementCount": len(set(used_markdown_ids)),
        "markdownDuplicateBindingCount": accepted - len(set(used_markdown_ids)),
        "buildReadyCount": int((build_contract.get("summary") or {}).get("readyCount") or 0),
        "buildUnresolvedCount": int((build_contract.get("summary") or {}).get("unresolvedCount") or 0),
    })
    result["summary"] = summary
    return result


__all__ = ["build_lines_first_markdown_augmented_contract"]
