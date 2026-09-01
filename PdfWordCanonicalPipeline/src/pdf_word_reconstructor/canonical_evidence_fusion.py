from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from rapidfuzz import fuzz

from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map

from .common import normalize_text
from .mathpix_lines_input import build_mathpix_line_layout_map

VERSION = "canonical-evidence-fusion-0.1"

_TEXTUAL_LINE_TYPES = {"text", "section_header", "figure_label"}
_STRUCTURAL_LINE_TYPES = {"page_info", "column"}
_IMAGE_TYPES = {"image", "figure", "diagram"}
_MATH_TYPES = {"display_equation", "equation", "math"}
_HEADING_TYPES = {"heading", "title", "section_header"}


def _bbox(record: dict[str, Any]) -> list[float] | None:
    raw = record.get("bbox_px") if isinstance(record.get("bbox_px"), dict) else None
    if not raw:
        return None
    try:
        box = [float(raw[k]) for k in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None
    return box if box[2] > box[0] and box[3] > box[1] else None


def _union(boxes: Iterable[list[float]]) -> list[float] | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes), min(b[1] for b in boxes),
        max(b[2] for b in boxes), max(b[3] for b in boxes),
    ]


def _record_text(record: dict[str, Any]) -> str:
    auth = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
    for value in (
        auth.get("text"), auth.get("plainText"), record.get("text"), record.get("captionText"),
        record.get("alt"), record.get("latex"), auth.get("rawMarkdown"), record.get("rawMarkdown"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _line_text(record: dict[str, Any]) -> str:
    for key in ("text_display", "text", "conversion_output"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _math_signature(text: str) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|left|right|displaystyle)", "", text)
    text = text.replace("−", "-").replace("–", "-").replace("—", "-").replace("⋅", "*").replace("·", "*").replace("×", "*")
    return re.sub(r"[^\w+\-*/=<>]", "", text, flags=re.UNICODE)


def _compatible(markdown_type: str, line_type: str) -> bool:
    a, b = str(markdown_type or ""), str(line_type or "")
    if a in _IMAGE_TYPES or b in _IMAGE_TYPES:
        return a in _IMAGE_TYPES and b in _IMAGE_TYPES
    if a in _MATH_TYPES or b in _MATH_TYPES:
        return a in _MATH_TYPES and b in _MATH_TYPES
    if a in _HEADING_TYPES or b in _HEADING_TYPES:
        return a in _HEADING_TYPES and b in _HEADING_TYPES
    if a == "caption" or b == "figure_label":
        return a in {"caption", "paragraph"} and b in {"figure_label", "text"}
    return True


def _text_score(a: str, b: str, kind: str = "") -> float:
    if kind in _MATH_TYPES:
        a2, b2 = _math_signature(a), _math_signature(b)
    else:
        a2, b2 = normalize_text(a), normalize_text(b)
    if not a2 or not b2:
        return 0.0
    if a2 == b2:
        return 100.0
    ratio = float(fuzz.ratio(a2, b2))
    partial = float(fuzz.partial_ratio(a2, b2))
    token = float(fuzz.token_set_ratio(a2, b2))
    shorter, longer = (a2, b2) if len(a2) <= len(b2) else (b2, a2)
    containment = 96.0 if len(shorter) >= 10 and shorter in longer else 0.0
    length_ratio = min(len(a2), len(b2)) / max(1, max(len(a2), len(b2)))
    return min(100.0, max(containment, 0.38 * ratio + 0.32 * partial + 0.30 * token) * (0.82 + 0.18 * length_ratio))


def _semantic_type(markdown_types: list[str], line_kind: str) -> tuple[str, list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    md = [str(v or "") for v in markdown_types if v]
    preferred = md[0] if md else ""
    if line_kind == "diagram":
        line_semantic = "figure"
    elif line_kind == "math":
        line_semantic = "equation"
    elif line_kind == "section_header":
        line_semantic = "heading"
    elif line_kind == "figure_label":
        line_semantic = "caption"
    else:
        line_semantic = "paragraph"
    md_semantic = {
        "display_equation": "equation", "equation": "equation", "math": "equation",
        "image": "figure", "figure": "figure", "diagram": "figure",
        "heading": "heading", "title": "heading", "section_header": "heading",
        "caption": "caption", "paragraph": "paragraph", "text": "paragraph",
    }.get(preferred, preferred or line_semantic)
    if md_semantic != line_semantic and md_semantic and line_semantic:
        conflicts.append({
            "attribute": "semantic.type", "markdown": md_semantic, "lines": line_semantic,
            "status": "explicit-conflict-no-silent-overwrite",
        })
    return md_semantic or line_semantic, conflicts


def _join_lines(rows: list[dict[str, Any]]) -> str:
    text = ""
    for row in rows:
        piece = _line_text(row)
        subtype = str(row.get("subtype") or "")
        if not text:
            text = piece
        elif subtype == "continues_line_no_hyphen":
            text = text.rstrip("-") + piece.lstrip()
        elif subtype == "continues_line_newline":
            text += "\n" + piece.lstrip()
        else:
            text += " " + piece.lstrip()
    return text.strip()


def build_lines_semantic_units(lines_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    line_map = build_mathpix_line_layout_map(Path(lines_path), None)
    units: list[dict[str, Any]] = []
    raw_count = 0
    type_counts: Counter[str] = Counter()

    for page in line_map.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        records = list(page.get("objects", []) or [])
        raw_count += len(records)
        type_counts.update(str(r.get("type") or "unknown") for r in records)
        by_id = {str(r.get("id")): r for r in records if r.get("id")}
        columns = [r for r in records if str(r.get("type") or "") == "column" and _bbox(r)]
        column_ids = {str(r.get("id")) for r in columns if r.get("id")}

        def column_ancestor(row: dict[str, Any]) -> str | None:
            seen: set[str] = set()
            parent = str(row.get("parent_id") or "")
            while parent and parent not in seen:
                seen.add(parent)
                if parent in column_ids:
                    return parent
                node = by_id.get(parent)
                if not node:
                    return None
                parent = str(node.get("parent_id") or "")
            return None

        current: list[dict[str, Any]] = []

        def flush() -> None:
            nonlocal current
            if not current:
                return
            first = current[0]
            boxes = [_bbox(r) for r in current]
            box = _union([b for b in boxes if b])
            units.append({
                "id": f"lines-unit-{page_no}-{len(units):05d}",
                "page": page_no,
                "kind": str(first.get("type") or "text"),
                "text": _join_lines(current),
                "bboxPx": box,
                "lineIds": [str(r.get("id") or "") for r in current],
                "lineNumbers": [int(r.get("line") or 0) for r in current],
                "subtypes": [str(r.get("subtype") or "") or None for r in current],
                "fontSizes": [r.get("font_size") for r in current if r.get("font_size") is not None],
                "parentIds": sorted({str(r.get("parent_id") or "") for r in current if r.get("parent_id")}),
                "zoneId": column_ancestor(first),
                "source": "mathpix-lines",
            })
            current = []

        for row in sorted(records, key=lambda r: (int(r.get("line") or 10**9), (_bbox(r) or [0, 0, 0, 0])[1])):
            kind = str(row.get("type") or "")
            if kind in _STRUCTURAL_LINE_TYPES or not _bbox(row):
                continue
            if kind in {"diagram", "math", "figure_label", "section_header"}:
                flush()
                units.append({
                    "id": f"lines-unit-{page_no}-{len(units):05d}",
                    "page": page_no,
                    "kind": kind,
                    "text": _line_text(row),
                    "bboxPx": _bbox(row),
                    "lineIds": [str(row.get("id") or "")],
                    "lineNumbers": [int(row.get("line") or 0)],
                    "subtypes": [str(row.get("subtype") or "") or None],
                    "fontSizes": [row.get("font_size")] if row.get("font_size") is not None else [],
                    "parentIds": [str(row.get("parent_id"))] if row.get("parent_id") else [],
                    "zoneId": column_ancestor(row),
                    "source": "mathpix-lines",
                })
                continue
            subtype = str(row.get("subtype") or "")
            same_zone = not current or column_ancestor(row) == column_ancestor(current[0])
            if current and (not subtype.startswith("continues_line") or not same_zone):
                flush()
            current.append(row)
        flush()

    return units, {
        "rawObjectCount": raw_count,
        "rawTypeCounts": dict(sorted(type_counts.items())),
        "semanticUnitCount": len(units),
        "semanticUnitTypes": dict(sorted(Counter(str(u.get("kind")) for u in units).items())),
    }


def _combined_text(records: list[dict[str, Any]], accessor) -> str:
    return "\n".join(str(accessor(r) or "").strip() for r in records if str(accessor(r) or "").strip()).strip()


def _pair_score(mmd_records: list[dict[str, Any]], line_units: list[dict[str, Any]]) -> float:
    if not mmd_records or not line_units:
        return 0.0
    md_types = [str(r.get("type") or "") for r in mmd_records]
    line_types = [str(r.get("kind") or "") for r in line_units]
    if len(set(md_types)) == 1 and len(set(line_types)) == 1 and not _compatible(md_types[0], line_types[0]):
        return 0.0
    md_text = _combined_text(mmd_records, _record_text)
    ln_text = _combined_text(line_units, lambda r: r.get("text"))
    kind = md_types[0] if len(set(md_types)) == 1 else ""
    score = _text_score(md_text, ln_text, kind)
    if len(mmd_records) > 1 or len(line_units) > 1:
        score -= 1.5 * (len(mmd_records) + len(line_units) - 2)
    return max(0.0, score)


def align_markdown_to_lines(markdown: list[dict[str, Any]], units: list[dict[str, Any]]) -> dict[str, Any]:
    """Monotonic sequence alignment with controlled 1↔2 block splits/merges.

    The alignment is global across the whole MMD and all Lines pages. This makes
    physical page assignment a result of Lines geometry instead of guessed MMD
    page boundaries.
    """
    m, n = len(markdown), len(units)
    neg = -10**12
    dp = [[neg] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[str, int, int, int, int, float] | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for i in range(1, m + 1):
        dp[i][0] = dp[i - 1][0] - 12.0
        back[i][0] = ("skip-mmd", i - 1, 0, 1, 0, 0.0)
    for j in range(1, n + 1):
        dp[0][j] = dp[0][j - 1] - 9.0
        back[0][j] = ("skip-lines", 0, j - 1, 0, 1, 0.0)

    for i in range(m + 1):
        for j in range(n + 1):
            if dp[i][j] <= neg / 2:
                continue
            if i < m and dp[i][j] - 12.0 > dp[i + 1][j]:
                dp[i + 1][j] = dp[i][j] - 12.0
                back[i + 1][j] = ("skip-mmd", i, j, 1, 0, 0.0)
            if j < n and dp[i][j] - 9.0 > dp[i][j + 1]:
                dp[i][j + 1] = dp[i][j] - 9.0
                back[i][j + 1] = ("skip-lines", i, j, 0, 1, 0.0)
            for mi, lj in ((1, 1), (1, 2), (2, 1)):
                if i + mi > m or j + lj > n:
                    continue
                score = _pair_score(markdown[i:i + mi], units[j:j + lj])
                threshold = 50.0 if (mi, lj) == (1, 1) else 72.0
                if score < threshold:
                    continue
                reward = score - 48.0
                if dp[i][j] + reward > dp[i + mi][j + lj]:
                    dp[i + mi][j + lj] = dp[i][j] + reward
                    back[i + mi][j + lj] = ("match", i, j, mi, lj, score)

    steps: list[dict[str, Any]] = []
    i, j = m, n
    while i or j:
        step = back[i][j]
        if step is None:
            break
        op, pi, pj, mi, lj, score = step
        steps.append({"op": op, "mmdStart": pi, "linesStart": pj, "mmdCount": mi, "linesCount": lj, "score": round(score, 2)})
        i, j = pi, pj
    steps.reverse()
    return {"steps": steps, "score": round(dp[m][n], 3)}


def _pdf_visual_containers(pdf_path: Path, pdf_page_index: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        return [], {"available": False, "reason": f"PyMuPDF unavailable: {exc}"}

    doc = fitz.open(str(pdf_path))
    if pdf_page_index < 0 or pdf_page_index >= len(doc):
        doc.close()
        return [], {"available": False, "reason": "page-index-out-of-range"}
    page = doc[pdf_page_index]
    page_rect = page.rect
    drawings = page.get_drawings()
    text_chars = len(page.get_text("text") or "")
    image_count = len(page.get_images(full=True))
    grouped: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    page_area = max(1.0, float(page_rect.width * page_rect.height))

    for drawing in drawings:
        rect = drawing.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = map(float, (rect.x0, rect.y0, rect.x1, rect.y1))
        width, height = x1 - x0, y1 - y0
        area_fraction = max(0.0, width * height / page_area)
        if width < 18.0 or height < 12.0 or area_fraction < 0.002 or area_fraction > 0.92:
            continue
        if drawing.get("color") is None and drawing.get("fill") is None:
            continue
        key = tuple(round(v) for v in (x0, y0, x1, y1))
        grouped[key].append(drawing)

    containers: list[dict[str, Any]] = []
    for key, rows in grouped.items():
        stroke = next((r.get("color") for r in rows if r.get("color") is not None), None)
        fill = next((r.get("fill") for r in rows if r.get("fill") is not None), None)
        width = max((float(r.get("width") or 0.0) for r in rows), default=0.0)
        containers.append({
            "id": f"pdf-container-{pdf_page_index + 1}-{len(containers):04d}",
            "bboxPt": [float(v) for v in key],
            "stroke": list(stroke) if isinstance(stroke, (tuple, list)) else stroke,
            "fill": list(fill) if isinstance(fill, (tuple, list)) else fill,
            "strokeWidthPt": width,
            "drawingCount": len(rows),
            "role": "visual-container-evidence-only",
        })
    containers.sort(key=lambda r: (r["bboxPt"][1], r["bboxPt"][0]))
    profile = {
        "available": True,
        "pageWidthPt": float(page_rect.width), "pageHeightPt": float(page_rect.height),
        "textChars": text_chars, "drawingCount": len(drawings), "imageCount": image_count,
        "containerCandidateCount": len(containers),
    }
    doc.close()
    return containers, profile


def _intersection_fraction(inner: list[float], outer: list[float]) -> float:
    x0, y0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    x1, y1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = max(1e-9, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return inter / area


def _confidence(score: float) -> str:
    if score >= 92:
        return "high"
    if score >= 78:
        return "medium"
    if score >= 62:
        return "low"
    return "unresolved"


def build_canonical_evidence_document(
    mmd_path: Path,
    lines_path: Path,
    pdf_path: Path | None = None,
    target_page: int | None = None,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    work_dir = Path(work_dir or Path(lines_path).parent)
    work_dir.mkdir(parents=True, exist_ok=True)
    markdown_map = extract_markdown_element_map([Path(mmd_path)], work_dir / "_fusion_markdown_map.json")
    markdown = list(markdown_map.get("records", []) or [])
    units, lines_inventory = build_lines_semantic_units(Path(lines_path))
    alignment = align_markdown_to_lines(markdown, units)

    blocks: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    mmd_accounted: set[int] = set()
    lines_accounted: set[int] = set()
    split_audit: list[dict[str, Any]] = []

    for step in alignment["steps"]:
        if step["op"] != "match":
            continue
        mi, lj = int(step["mmdCount"]), int(step["linesCount"])
        ms, ls = int(step["mmdStart"]), int(step["linesStart"])
        mmd_rows = markdown[ms:ms + mi]
        line_rows = units[ls:ls + lj]
        mmd_accounted.update(range(ms, ms + mi))
        lines_accounted.update(range(ls, ls + lj))
        if lj > 1:
            split_audit.append({
                "markdownIds": [r.get("id") for r in mmd_rows],
                "linesUnitIds": [r.get("id") for r in line_rows],
                "reason": "multiple-independent-Lines-units-match-one-Markdown-span",
                "score": step["score"],
            })
        for line_row in line_rows:
            semantic_type, conflicts = _semantic_type([str(r.get("type") or "") for r in mmd_rows], str(line_row.get("kind") or ""))
            line_text = str(line_row.get("text") or "")
            if len(line_rows) == 1:
                content_text = _combined_text(mmd_rows, _record_text) or line_text
                content_source = "mathpix-markdown-primary"
            else:
                content_text = line_text
                content_source = "mathpix-lines-segment-with-markdown-parent-corroboration"
            block = {
                "id": f"canonical-{len(blocks):05d}",
                "semantic": {
                    "type": semantic_type,
                    "source": "mathpix-markdown-primary-lines-corroborated",
                    "confidence": _confidence(float(step["score"])),
                },
                "content": {
                    "text": content_text,
                    "source": content_source,
                    "markdownIds": [r.get("id") for r in mmd_rows],
                    "rawMarkdown": "\n".join(str(r.get("rawMarkdown") or "") for r in mmd_rows),
                },
                "pageAssignment": {
                    "physicalPage": int(line_row.get("page") or 0),
                    "source": "mathpix-lines-geometry",
                    "confidence": "high",
                },
                "geometry": {
                    "bboxPx": line_row.get("bboxPx"), "zoneId": line_row.get("zoneId"),
                    "lineIds": line_row.get("lineIds") or [], "lineNumbers": line_row.get("lineNumbers") or [],
                    "source": "mathpix-lines",
                },
                "typographyEvidence": {
                    "fontSizes": line_row.get("fontSizes") or [],
                    "subtypes": line_row.get("subtypes") or [],
                    "source": "mathpix-lines",
                },
                "visualEvidence": {"pdfContainerIds": []},
                "relations": {"belongsToGroups": []},
                "evidence": {
                    "alignmentScore": step["score"],
                    "agreements": ["markdown-lines-content/order"],
                    "conflicts": conflicts,
                },
                "wordRealization": None,
            }
            blocks.append(block)

    line_pages = sorted({int(u.get("page") or 0) for u in units if int(u.get("page") or 0)})
    pdf_profiles: dict[str, Any] = {}
    pdf_containers: list[dict[str, Any]] = []
    if pdf_path is not None and Path(pdf_path).exists():
        for ordinal, page_no in enumerate(line_pages):
            if target_page is not None and page_no != target_page:
                continue
            containers, profile = _pdf_visual_containers(Path(pdf_path), ordinal)
            pdf_profiles[str(page_no)] = profile
            scale_x = None
            scale_y = None
            page_units = [u for u in units if int(u.get("page") or 0) == page_no]
            if page_units and profile.get("available"):
                # Recover Lines page extent from the source map rather than from block union.
                line_map = build_mathpix_line_layout_map(Path(lines_path), None)
                source_page = next((p for p in line_map.get("pages", []) or [] if int(p.get("page") or 0) == page_no), None)
                if source_page:
                    pw = float(source_page.get("page_width_px") or 0.0)
                    ph = float(source_page.get("page_height_px") or 0.0)
                    if pw > 0 and ph > 0:
                        scale_x = float(profile["pageWidthPt"]) / pw
                        scale_y = float(profile["pageHeightPt"]) / ph
            for container in containers:
                container["physicalPage"] = page_no
                pdf_containers.append(container)
                if scale_x is None or scale_y is None:
                    continue
                members: list[str] = []
                for block in blocks:
                    if int((block.get("pageAssignment") or {}).get("physicalPage") or 0) != page_no:
                        continue
                    b = (block.get("geometry") or {}).get("bboxPx")
                    if not b:
                        continue
                    block_pt = [b[0] * scale_x, b[1] * scale_y, b[2] * scale_x, b[3] * scale_y]
                    if _intersection_fraction(block_pt, container["bboxPt"]) >= 0.78:
                        members.append(block["id"])
                if not members:
                    continue
                group_id = f"group-{len(groups):05d}"
                groups.append({
                    "id": group_id,
                    "type": "visual-container",
                    "physicalPage": page_no,
                    "memberBlockIds": members,
                    "bboxPt": container["bboxPt"],
                    "evidence": [{"source": "pdf-drawing-enclosure", "containerId": container["id"], "confidence": "medium"}],
                    "wordRealization": None,
                })
                for block in blocks:
                    if block["id"] in members:
                        block["relations"]["belongsToGroups"].append(group_id)
                        block["visualEvidence"]["pdfContainerIds"].append(container["id"])

    selected_blocks = blocks if target_page is None else [b for b in blocks if int((b.get("pageAssignment") or {}).get("physicalPage") or 0) == target_page]
    selected_groups = groups if target_page is None else [g for g in groups if int(g.get("physicalPage") or 0) == target_page]
    selected_containers = pdf_containers if target_page is None else [c for c in pdf_containers if int(c.get("physicalPage") or 0) == target_page]

    unmatched_mmd = [
        {"index": i, "id": row.get("id"), "type": row.get("type"), "textPreview": _record_text(row)[:160]}
        for i, row in enumerate(markdown) if i not in mmd_accounted
    ]
    unmatched_lines = [
        {"index": i, "id": row.get("id"), "page": row.get("page"), "kind": row.get("kind"), "textPreview": str(row.get("text") or "")[:160]}
        for i, row in enumerate(units) if i not in lines_accounted
    ]

    return {
        "version": VERSION,
        "status": "canonical-evidence-only-no-word-decisions",
        "policy": {
            "semanticPrimary": "mathpix-markdown",
            "geometryPrimary": "mathpix-lines",
            "pageAssignmentPrimary": "mathpix-lines",
            "pdfRole": "visual-truth-witness-no-semantic-labeling",
            "conflicts": "explicit-no-silent-overwrite",
            "wordRealization": "forbidden-at-this-stage",
        },
        "sources": {"mmd": str(mmd_path), "lines": str(lines_path), "pdf": str(pdf_path) if pdf_path else None},
        "targetPage": target_page,
        "linesInventory": lines_inventory,
        "pdfWitnessProfiles": pdf_profiles,
        "blocks": selected_blocks,
        "groups": selected_groups,
        "pdfContainers": selected_containers,
        "audits": {
            "mmdSplit": split_audit,
            "unmatchedMarkdown": unmatched_mmd,
            "unmatchedLines": unmatched_lines,
        },
        "summary": {
            "markdownRecordCount": len(markdown), "linesUnitCount": len(units),
            "canonicalBlockCount": len(selected_blocks), "groupCount": len(selected_groups),
            "matchedMarkdownCount": len(mmd_accounted), "matchedLinesUnitCount": len(lines_accounted),
            "unmatchedMarkdownCount": len(unmatched_mmd), "unmatchedLinesUnitCount": len(unmatched_lines),
            "conflictBlockCount": sum(bool((b.get("evidence") or {}).get("conflicts")) for b in selected_blocks),
            "wordDecisionCount": sum(b.get("wordRealization") is not None for b in selected_blocks) + sum(g.get("wordRealization") is not None for g in selected_groups),
        },
    }


__all__ = [
    "VERSION", "build_lines_semantic_units", "align_markdown_to_lines", "build_canonical_evidence_document",
]
