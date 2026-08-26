from __future__ import annotations

from collections import defaultdict
import json
import re
import shutil
from pathlib import Path
from statistics import median
from typing import Any

from PIL import Image

from .asset_resolver import (
    build_asset_catalog,
    match_image_asset,
    match_positioned_asset,
    materialize_asset,
)



def _equation_signature(text: str) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:left|right|displaystyle|quad|qquad)", "", text)
    text = re.sub(r"[^0-9a-zα-ωά-ώ+\-=/<>]", "", text)
    return text

def _load_equation_donors(path: Path | None) -> tuple[dict[int, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    records: list[dict[str, Any]] = []
    if not path or not Path(path).exists():
        return by_page, records
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for index, rec in enumerate(data.get("records", [])):
            rec = dict(rec)
            rec["_donor_index"] = index
            records.append(rec)
            if rec.get("page") is not None:
                by_page[int(rec["page"])].append(rec)
    except Exception:
        pass
    return by_page, records

def _attach_ordered_equation_donor_trail(
    result_pages: list[dict[str, Any]],
    donor_records: list[dict[str, Any]],
    used_equation_donors: set[str],
) -> int:
    """Recover Markdown donors when Mathpix page numbers are offset or unreliable.

    Page-local matching is still preferred. This pass is deliberately second and
    monotonic: it treats the Markdown file as an ordered donor stream, then
    records the distance/page offset so the fallback is auditable.
    """
    pending: list[tuple[int, dict[str, Any]]] = []
    for page in result_pages:
        page_no = int(page.get("page") or 0)
        for item in page.get("flow", []):
            if item.get("semantic_type") == "equation" and not item.get("markdown_latex"):
                pending.append((page_no, item))
    donors = [d for d in donor_records if str(d.get("id")) not in used_equation_donors]
    if not pending or not donors:
        return 0

    from rapidfuzz import fuzz

    min_score = 20.0
    count_bonus = 8.0
    n = len(pending)
    m = len(donors)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    choice: list[list[tuple[str, int | None] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    scores: dict[tuple[int, int], float] = {}
    for i, (_page_no, item) in enumerate(pending, start=1):
        pdf_sig = _equation_signature(item.get("text", ""))
        for j, donor in enumerate(donors, start=1):
            donor_sig = str(donor.get("signature") or "")
            score = float(fuzz.ratio(pdf_sig, donor_sig)) if pdf_sig and donor_sig else 0.0
            scores[(i, j)] = score
            best = dp[i][j - 1]
            best_choice: tuple[str, int | None] = ("skip-donor", None)
            if dp[i - 1][j] > best:
                best = dp[i - 1][j]
                best_choice = ("skip-equation", None)
            if score >= min_score:
                matched = dp[i - 1][j - 1] + score + count_bonus
                if matched > best:
                    best = matched
                    best_choice = ("match", j - 1)
            dp[i][j] = best
            choice[i][j] = best_choice

    matches: list[tuple[int, int, float]] = []
    i, j = n, m
    while i > 0 and j > 0:
        action = choice[i][j]
        if not action:
            break
        if action[0] == "match":
            donor_idx = int(action[1] or 0)
            matches.append((i - 1, donor_idx, scores.get((i, j), 0.0)))
            i -= 1
            j -= 1
        elif action[0] == "skip-equation":
            i -= 1
        else:
            j -= 1
    matches.reverse()
    if not matches:
        return 0
    avg_score = sum(score for _eq_idx, _donor_idx, score in matches) / len(matches)
    if len(matches) < min(2, len(pending)) and avg_score < 50.0:
        return 0
    if avg_score < 24.0:
        return 0

    attached = 0
    previous_donor_index = -1
    for eq_idx, donor_idx, score in matches:
        page_no, item = pending[eq_idx]
        donor = donors[donor_idx]
        donor_id = str(donor.get("id"))
        if donor_id in used_equation_donors:
            continue
        donor_page = donor.get("page")
        item["markdown_latex"] = donor.get("latex")
        item["markdown_equation_donor"] = {
            "id": donor.get("id"),
            "score": round(float(score), 2),
            "matchMode": "ordered-donor-trail",
            "source": donor.get("source"),
            "pageConfidence": donor.get("pageConfidence"),
            "donorPage": donor_page,
            "pageOffset": int(donor_page) - page_no if donor_page is not None else None,
            "donorIndex": donor.get("_donor_index"),
            "trailDistance": int(donor.get("_donor_index") or 0) - previous_donor_index,
        }
        previous_donor_index = int(donor.get("_donor_index") or 0)
        used_equation_donors.add(donor_id)
        attached += 1
    return attached

def _build_markdown_equation_map(
    result_pages: list[dict[str, Any]],
    donor_records: list[dict[str, Any]],
) -> dict[str, Any]:
    equations: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    for page in result_pages:
        page_no = int(page.get("page") or 0)
        for item in page.get("flow", []):
            if item.get("semantic_type") != "equation":
                continue
            donor = dict(item.get("markdown_equation_donor") or {})
            donor_id = str(donor.get("id") or "")
            if donor_id:
                matched_ids.add(donor_id)
            equations.append({
                "page": page_no,
                "id": item.get("id"),
                "pdfSignature": _equation_signature(item.get("text", "")),
                "matched": bool(donor_id),
                "donor": donor or None,
                "latexPreview": str(item.get("markdown_latex") or "")[:240],
            })
    unused = []
    for donor in donor_records:
        donor_id = str(donor.get("id"))
        if donor_id in matched_ids:
            continue
        unused.append({
            "id": donor.get("id"),
            "donorIndex": donor.get("_donor_index"),
            "page": donor.get("page"),
            "pageConfidence": donor.get("pageConfidence"),
            "signature": donor.get("signature"),
            "latexPreview": str(donor.get("latex") or "")[:240],
        })
    return {
        "version": "markdown-equation-map-0.1",
        "equationCount": len(equations),
        "matchedEquationCount": sum(1 for item in equations if item.get("matched")),
        "unmatchedEquationCount": sum(1 for item in equations if not item.get("matched")),
        "donorCount": len(donor_records),
        "unusedDonorCount": len(unused),
        "equations": equations,
        "unusedDonors": unused,
    }

def _area(box: list[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _union(boxes: list[list[float]]) -> list[float]:
    return [
        min(float(b[0]) for b in boxes),
        min(float(b[1]) for b in boxes),
        max(float(b[2]) for b in boxes),
        max(float(b[3]) for b in boxes),
    ]


def _intersection(a: list[float], b: list[float]) -> float:
    x0, y0 = max(float(a[0]), float(b[0])), max(float(a[1]), float(b[1]))
    x1, y1 = min(float(a[2]), float(b[2])), min(float(a[3]), float(b[3]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _overlap_ratio(a: list[float], b: list[float], denominator: str = "min") -> float:
    inter = _intersection(a, b)
    if denominator == "a":
        den = _area(a)
    elif denominator == "b":
        den = _area(b)
    else:
        den = min(_area(a), _area(b))
    return inter / max(1.0, den)


def _gaps(a: list[float], b: list[float]) -> tuple[float, float]:
    dx = max(0.0, max(float(a[0]), float(b[0])) - min(float(a[2]), float(b[2])))
    dy = max(0.0, max(float(a[1]), float(b[1])) - min(float(a[3]), float(b[3])))
    return dx, dy


def _axis_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _contains(big: list[float], small: list[float], ratio: float = 0.88) -> bool:
    return _overlap_ratio(small, big, denominator="a") >= ratio


def _meaningful_images(page: dict[str, Any]) -> list[dict[str, Any]]:
    images = [r for r in page.get("regions", []) if r.get("type") == "image" and _area(r.get("bbox", [0, 0, 0, 0])) >= 20.0]
    images.sort(key=lambda r: _area(r["bbox"]), reverse=True)
    kept: list[dict[str, Any]] = []
    for image in images:
        if any(_contains(other["bbox"], image["bbox"], 0.86) for other in kept):
            continue
        kept.append(image)
    return kept


def _should_join(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ab, bb = a["bbox"], b["bbox"]
    if _overlap_ratio(ab, bb) > 0.05:
        return True
    dx, dy = _gaps(ab, bb)
    x_overlap = _axis_overlap(ab[0], ab[2], bb[0], bb[2])
    y_overlap = _axis_overlap(ab[1], ab[3], bb[1], bb[3])
    min_w = max(1.0, min(ab[2] - ab[0], bb[2] - bb[0]))
    min_h = max(1.0, min(ab[3] - ab[1], bb[3] - bb[1]))
    x_ratio, y_ratio = x_overlap / min_w, y_overlap / min_h
    kinds = {a["kind"], b["kind"]}

    if "image" in kinds and dx <= 18.0 and y_ratio >= 0.18:
        return True
    if "image" in kinds and dy <= 20.0 and x_ratio >= 0.18:
        return True
    if kinds <= {"equation"}:
        ac = (ab[0] + ab[2]) / 2
        bc = (bb[0] + bb[2]) / 2
        if dy <= 38.0 and (x_ratio >= 0.08 or abs(ac - bc) <= 75.0):
            return True
        if dx <= 24.0 and y_ratio >= 0.15:
            return True
    if "figure_label" in kinds and (dx <= 24.0 and y_ratio >= 0.05 or dy <= 24.0 and x_ratio >= 0.05):
        return True
    return False


def _group_visual_candidates(page: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for image in _meaningful_images(page):
        candidates.append({"id": image["id"], "kind": "image", "bbox": image["bbox"]})
    for region in page.get("regions", []):
        if region.get("type") != "text":
            continue
        kind = region.get("semantic", {}).get("type")
        if kind in {"equation", "figure_label"}:
            candidates.append({"id": region["id"], "kind": kind, "bbox": region["bbox"]})

    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if _should_join(candidates[i], candidates[j]):
                union(i, j)

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, candidate in enumerate(candidates):
        grouped[find(i)].append(candidate)

    # A body paragraph can sit between two equations that are close in PDF
    # coordinates. Do not merge across that semantic barrier (page 20 is the
    # canonical example: one equation, prose, then a second calculation).
    barriers = []
    for region in page.get("regions", []):
        if region.get("type") != "text":
            continue
        sem = region.get("semantic", {})
        if sem.get("type") in {"body", "heading", "caption"} and sem.get("flow_zone") == "main":
            barriers.append(region.get("bbox", [0, 0, 0, 0]))

    expanded_groups: list[list[dict[str, Any]]] = []
    for members in grouped.values():
        kinds = {m["kind"] for m in members}
        if kinds <= {"equation"} and len(members) > 1:
            group_box = _union([m["bbox"] for m in members])
            relevant = []
            for barrier in barriers:
                if barrier[1] <= group_box[1] or barrier[3] >= group_box[3]:
                    continue
                x_overlap = _axis_overlap(group_box[0], group_box[2], barrier[0], barrier[2])
                if x_overlap / max(1.0, min(group_box[2] - group_box[0], barrier[2] - barrier[0])) >= 0.18:
                    relevant.append(barrier)
            if relevant:
                relevant.sort(key=lambda b: b[1])
                segments: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for member in members:
                    center_y = (member["bbox"][1] + member["bbox"][3]) / 2
                    segment = sum(1 for barrier in relevant if center_y > barrier[3])
                    segments[segment].append(member)
                expanded_groups.extend(segment for _, segment in sorted(segments.items()) if segment)
                continue
        expanded_groups.append(members)

    results: list[dict[str, Any]] = []
    for members in expanded_groups:
        bbox = _union([m["bbox"] for m in members])
        kinds = {m["kind"] for m in members}
        kind = "figure" if "image" in kinds or "figure_label" in kinds else "equation"
        if kind == "figure" and _area(bbox) < 80.0:
            continue
        results.append({
            "id": "vg-" + "-".join(m["id"] for m in members[:4]),
            "kind": kind,
            "bbox": [round(v, 3) for v in bbox],
            "member_ids": [m["id"] for m in members],
            "member_kinds": sorted(kinds),
        })
    return _merge_adjacent_figure_groups(results)


def _merge_adjacent_figure_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join vertically adjacent pieces of one raster diagram.

    PDF exporters frequently split a single diagram into a large image group and a
    thin bottom strip. Without this pass Word receives a duplicate-looking fragment.
    """
    ordered = sorted(groups, key=lambda g: (float(g["bbox"][1]), float(g["bbox"][0])))
    changed = True
    while changed:
        changed = False
        result: list[dict[str, Any]] = []
        used = set()
        for i, current in enumerate(ordered):
            if i in used:
                continue
            merged = dict(current)
            merged["member_ids"] = list(current.get("member_ids", []))
            merged["member_kinds"] = list(current.get("member_kinds", []))
            for j in range(i + 1, len(ordered)):
                if j in used:
                    continue
                other = ordered[j]
                if merged.get("kind") != "figure" or other.get("kind") != "figure":
                    continue
                a, b = merged["bbox"], other["bbox"]
                vertical_gap = max(0.0, float(b[1]) - float(a[3]))
                x_overlap = _axis_overlap(float(a[0]), float(a[2]), float(b[0]), float(b[2]))
                min_width = max(1.0, min(float(a[2]) - float(a[0]), float(b[2]) - float(b[0])))
                if vertical_gap <= 8.0 and x_overlap / min_width >= 0.30:
                    merged["bbox"] = _union([a, b])
                    merged["member_ids"].extend(other.get("member_ids", []))
                    merged["member_kinds"] = sorted(set(merged["member_kinds"]) | set(other.get("member_kinds", [])))
                    merged["id"] = "vg-" + "-".join(merged["member_ids"][:4])
                    used.add(j)
                    changed = True
            result.append(merged)
        ordered = sorted(result, key=lambda g: (float(g["bbox"][1]), float(g["bbox"][0])))
    return ordered


def _weighted_median(values: list[tuple[float, int]]) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    total = sum(max(1, w) for _, w in values)
    acc = 0
    for value, weight in values:
        acc += max(1, weight)
        if acc >= total / 2:
            return float(value)
    return float(values[-1][0])



def _line_text(line: dict[str, Any]) -> str:
    return "".join(str(span.get("text", "")) for span in line.get("spans", [])).strip()


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    index = round((len(ordered) - 1) * max(0.0, min(1.0, fraction)))
    return ordered[index]


def _detect_columns(page: dict[str, Any]) -> tuple[list[dict[str, float]], dict[str, Any]]:
    """Detect genuine equal-width two-column prose from line coordinates.

    Equal physical widths are a defining characteristic of the exercise pages in
    the source material.  We therefore use robust 10/90-percentile boundaries
    instead of the longest line.  A single long formula or answer line may cross
    the nominal right edge but must not widen one column or turn the page into an
    absolute-layout composition.
    """
    width = float(page.get("width_pt") or 595.0)
    center = width / 2.0
    left: list[tuple[list[float], int]] = []
    right: list[tuple[list[float], int]] = []
    for region in page.get("regions", []):
        if region.get("type") != "text":
            continue
        sem = region.get("semantic", {})
        if sem.get("flow_zone") != "main" or sem.get("type") not in {"body", "heading", "caption"}:
            continue
        for line in region.get("lines", []):
            box = list(map(float, line.get("bbox", [0, 0, 0, 0])))
            text = _line_text(line)
            weight = len(text.strip())
            if weight < 2:
                continue
            if box[2] <= center + 8.0:
                left.append((box, weight))
            elif box[0] >= center - 8.0:
                right.append((box, weight))

    diagnostics: dict[str, Any] = {
        "candidate": False,
        "accepted": False,
        "reason": "insufficient-two-sided-prose",
        "leftLineCount": len(left),
        "rightLineCount": len(right),
        "equalWidthTolerance": 0.08,
    }
    left_chars = sum(w for _, w in left)
    right_chars = sum(w for _, w in right)
    diagnostics.update({"leftChars": left_chars, "rightChars": right_chars})
    if len(left) < 5 or len(right) < 5 or left_chars < 220 or right_chars < 220:
        return [], diagnostics

    diagnostics["candidate"] = True
    left_x0 = _quantile([b[0] for b, _ in left], 0.10)
    left_x1 = _quantile([b[2] for b, _ in left], 0.90)
    right_x0 = _quantile([b[0] for b, _ in right], 0.10)
    right_x1 = _quantile([b[2] for b, _ in right], 0.90)
    left_width = max(1.0, left_x1 - left_x0)
    right_width = max(1.0, right_x1 - right_x0)
    equal_ratio = min(left_width, right_width) / max(left_width, right_width)
    gutter = right_x0 - left_x1
    left_y0, left_y1 = min(b[1] for b, _ in left), max(b[3] for b, _ in left)
    right_y0, right_y1 = min(b[1] for b, _ in right), max(b[3] for b, _ in right)
    overlap_y = max(0.0, min(left_y1, right_y1) - max(left_y0, right_y0))
    vertical_overlap_ratio = overlap_y / max(1.0, min(left_y1 - left_y0, right_y1 - right_y0))
    content_balance = min(left_chars, right_chars) / max(1, max(left_chars, right_chars))
    diagnostics.update({
        "robustLeftWidthPt": round(left_width, 3),
        "robustRightWidthPt": round(right_width, 3),
        "equalWidthRatio": round(equal_ratio, 5),
        "gutterPt": round(gutter, 3),
        "verticalOverlapRatio": round(vertical_overlap_ratio, 5),
        "contentBalance": round(content_balance, 5),
    })

    if gutter < 8.0:
        diagnostics["reason"] = "no-real-gutter"
        return [], diagnostics
    if equal_ratio < 0.92:
        diagnostics["reason"] = "column-widths-not-equal"
        return [], diagnostics
    if vertical_overlap_ratio < 0.55:
        diagnostics["reason"] = "columns-do-not-share-a-common-vertical-band"
        return [], diagnostics
    if content_balance < 0.30:
        diagnostics["reason"] = "one-side-is-a-sidebar-not-a-column"
        return [], diagnostics

    equal_width = (left_width + right_width) / 2.0
    # Keep the robust origins, force equal widths and preserve the measured gutter.
    # Outlier lines beyond these boundaries are later classified as spanning lines.
    left_x1_equal = left_x0 + equal_width
    right_x1_equal = right_x0 + equal_width
    if right_x1_equal > width - 8.0:
        equal_width -= right_x1_equal - (width - 8.0)
        left_x1_equal = left_x0 + equal_width
        right_x1_equal = right_x0 + equal_width
    y0 = min(left_y0, right_y0)
    y1 = max(left_y1, right_y1)
    diagnostics.update({
        "accepted": True,
        "reason": "equal-width-two-column-flow",
        "normalizedColumnWidthPt": round(equal_width, 3),
    })
    return [
        {"x0": round(left_x0, 3), "x1": round(left_x1_equal, 3), "y0": round(y0, 3), "y1": round(y1, 3)},
        {"x0": round(right_x0, 3), "x1": round(right_x1_equal, 3), "y0": round(y0, 3), "y1": round(y1, 3)},
    ], diagnostics

def _split_region_for_columns(region: dict[str, Any], columns: list[dict[str, float]], page_width: float) -> list[dict[str, Any]]:
    if len(columns) != 2 or not region.get("lines"):
        return [region]
    left_x1 = float(columns[0]["x1"])
    right_x0 = float(columns[1]["x0"])
    center = (left_x1 + right_x0) / 2.0
    top_y = min(float(columns[0].get("y0", 0.0)), float(columns[1].get("y0", 0.0)))
    buckets: dict[str, list[dict[str, Any]]] = {"left": [], "right": [], "span": []}
    title_line_ids: set[int] = set()
    for line_index, line in enumerate(region.get("lines", [])):
        box = list(map(float, line.get("bbox", [0, 0, 0, 0])))
        width = box[2] - box[0]
        text = _line_text(line).strip()
        spans = line.get("spans", [])
        bold = any(int(span.get("flags", 0) or 0) & 16 for span in spans)
        colored = any(str(span.get("color", "#000000")).lower() not in {"#000000", "#000", "0"} for span in spans)
        line_center = (box[0] + box[2]) / 2.0
        centered_title = (
            bool(text) and len(text) <= 90 and bold and colored
            and abs(line_center - page_width / 2.0) <= page_width * 0.18
            and box[1] <= top_y + 28.0
        )
        crosses_gutter = box[0] < left_x1 - 1.0 and box[2] > right_x0 + 1.0
        if width >= page_width * 0.72 or crosses_gutter or centered_title:
            key = "span"
            if centered_title:
                title_line_ids.add(line_index)
        else:
            key = "left" if line_center < center else "right"
        line_copy = dict(line)
        line_copy["_source_line_index"] = line_index
        buckets[key].append(line_copy)
    outputs: list[dict[str, Any]] = []
    for key in ("span", "left", "right"):
        lines = buckets[key]
        if not lines:
            continue
        # Keep a centered blue/bold page title separate from any full-width
        # instruction line immediately below it, so it can receive heading style.
        if key == "span":
            title_lines = [line for line in lines if int(line.get("_source_line_index", -1)) in title_line_ids]
            ordinary_lines = [line for line in lines if int(line.get("_source_line_index", -1)) not in title_line_ids]
            groups = [("span-title", title_lines), ("span", ordinary_lines)]
        else:
            groups = [(key, lines)]
        for role, group_lines in groups:
            if not group_lines:
                continue
            cleaned_lines = []
            for line in group_lines:
                clone_line = dict(line)
                clone_line.pop("_source_line_index", None)
                cleaned_lines.append(clone_line)
            clone = dict(region)
            clone["semantic"] = dict(region.get("semantic", {}))
            clone["id"] = f"{region.get('id')}-{role}"
            clone["source_region_id"] = region.get("id")
            clone["column_role"] = "span" if role.startswith("span") else role
            clone["lines"] = cleaned_lines
            clone["bbox"] = _union([list(map(float, line.get("bbox", [0,0,0,0]))) for line in cleaned_lines])
            clone["text"] = "\n".join(_line_text(line) for line in cleaned_lines if _line_text(line))
            if role == "span-title":
                clone["semantic"]["type"] = "heading"
                clone["semantic"]["flow_zone"] = "main"
                # The PDF title is often a short centered line, but Word needs a
                # full-width frame to preserve it on one line above both columns.
                clone["bbox"] = [
                    float(columns[0]["x0"]), float(clone["bbox"][1]),
                    float(columns[1]["x1"]), float(clone["bbox"][3]),
                ]
            outputs.append(clone)
    return outputs or [region]

def _main_column(page: dict[str, Any]) -> dict[str, float]:
    main_regions = []
    for region in page.get("regions", []):
        if region.get("type") != "text":
            continue
        sem = region.get("semantic", {})
        if sem.get("flow_zone") != "main" or sem.get("type") not in {"body", "heading", "caption"}:
            continue
        text_len = max(1, len(str(region.get("text", "")).strip()))
        main_regions.append((region, text_len))
    if not main_regions:
        return {"x0": 36.0, "x1": float(page.get("width_pt", 595.0)) - 36.0, "y0": 55.0, "y1": 715.0}

    x0 = _weighted_median([(float(r["bbox"][0]), w) for r, w in main_regions])
    x1 = _weighted_median([(float(r["bbox"][2]), w) for r, w in main_regions])
    # Avoid a short centered heading narrowing the column.
    widths = sorted(float(r["bbox"][2]) - float(r["bbox"][0]) for r, _ in main_regions)
    expected_width = widths[len(widths) // 2]
    if x1 - x0 < expected_width * 0.9:
        candidates = [r for r, _ in main_regions if float(r["bbox"][2]) - float(r["bbox"][0]) >= expected_width * 0.9]
        if candidates:
            x0 = median(float(r["bbox"][0]) for r in candidates)
            x1 = median(float(r["bbox"][2]) for r in candidates)
    y0 = min(float(r["bbox"][1]) for r, _ in main_regions)
    y1 = max(float(r["bbox"][3]) for r, _ in main_regions)
    return {"x0": round(x0, 3), "x1": round(x1, 3), "y0": round(y0, 3), "y1": round(y1, 3)}


def _merge_flow_regions(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions = sorted(regions, key=lambda r: (float(r["bbox"][1]), float(r["bbox"][0])))
    merged: list[dict[str, Any]] = []
    for region in regions:
        kind = region.get("semantic", {}).get("type", "body")
        if merged:
            prev = merged[-1]
            prev_kind = prev["semantic_type"]
            gap = float(region["bbox"][1]) - float(prev["bbox"][3])
            x_overlap = _axis_overlap(prev["bbox"][0], prev["bbox"][2], region["bbox"][0], region["bbox"][2])
            min_width = max(1.0, min(prev["bbox"][2] - prev["bbox"][0], region["bbox"][2] - region["bbox"][0]))
            same_role = region.get("column_role") == prev.get("column_role")
            same_kind_continuation = (
                same_role
                and kind == prev_kind
                and kind in {"caption", "body"}
                and -1.0 <= gap <= 3.5
                and x_overlap / min_width >= 0.55
                and (kind == "caption" or len(prev["region_ids"]) < 2)
            )
            caption_tail = (
                same_role
                and prev_kind == "caption"
                and kind == "body"
                and -1.0 <= gap <= 4.0
                and x_overlap / min_width >= 0.55
                and len(str(region.get("text", "")).strip()) <= 240
            )
            if same_kind_continuation or caption_tail:
                prev["region_ids"].append(region["id"])
                prev["bbox"] = _union([prev["bbox"], region["bbox"]])
                prev["text"] = (prev["text"].rstrip() + "\n" + str(region.get("text", "")).lstrip()).strip()
                continue
        merged.append({
            "id": "flow-" + region["id"],
            "type": "text",
            "semantic_type": kind,
            "bbox": list(map(float, region["bbox"])),
            "region_ids": [region["id"]],
            "text": str(region.get("text", "")),
            "column_role": region.get("column_role"),
        })
    return merged


def _refine_equation_bbox_from_raster(
    page: dict[str, Any],
    group: dict[str, Any],
    column: dict[str, float],
    work_dir: Path,
) -> list[float]:
    """Recover the visual extent of a main-column equation from page pixels.

    Math-font text coordinates are often wrong horizontally. The vertical band is
    usually reliable, so we scan only that band. Short displayed equations are
    searched in the central part of the main column; long calculations stay close
    to their original x range. Sidebar equations are intentionally left untouched.
    """
    original = list(map(float, group["bbox"]))
    col_box = [float(column["x0"]), 0.0, float(column["x1"]), float(page.get("height_pt", 842.0))]
    if _overlap_ratio(original, col_box, denominator="a") < 0.18:
        return original
    render_path = work_dir / page["render"]
    if not render_path.exists():
        return original
    with Image.open(render_path).convert("L") as image:
        sx = image.width / float(page.get("width_pt") or 595.0)
        sy = image.height / float(page.get("height_pt") or 842.0)
        ox0, y0, ox1, y1 = original
        width = ox1 - ox0
        col_x0, col_x1 = float(column["x0"]), float(column["x1"])
        if width < 110.0:
            col_width = col_x1 - col_x0
            x0_pt = col_x0 + col_width * 0.08
            x1_pt = col_x1 - col_width * 0.08
        else:
            x0_pt = max(col_x0, ox0 - 10.0)
            x1_pt = min(col_x1, ox1 + 10.0)
        # Do not absorb the prose lines immediately above or below the formula.
        y0_pt = max(0.0, y0 + 0.2)
        y1_pt = min(float(page.get("height_pt") or 842.0), y1 - 0.6)
        if y1_pt <= y0_pt:
            return original
        px_box = (
            max(0, round(x0_pt * sx)),
            max(0, round(y0_pt * sy)),
            min(image.width, round(x1_pt * sx)),
            min(image.height, round(y1_pt * sy)),
        )
        band = image.crop(px_box)
        mask = band.point(lambda value: 255 if value < 242 else 0, mode="1")
        ink = mask.getbbox()
        if ink is None:
            return original
        ix0, iy0, ix1, iy1 = ink
        refined = [
            (px_box[0] + ix0) / sx - 1.5,
            (px_box[1] + iy0) / sy - 1.0,
            (px_box[0] + ix1) / sx + 1.5,
            (px_box[1] + iy1) / sy + 1.0,
        ]
        candidate = [
            round(max(col_x0, refined[0]), 3),
            round(max(0.0, refined[1]), 3),
            round(min(col_x1, refined[2]), 3),
            round(min(float(page.get("height_pt") or 842.0), refined[3]), 3),
        ]
        # Reject an implausibly wide band; that means adjacent prose leaked in.
        if candidate[2] - candidate[0] > (col_x1 - col_x0) * 0.82 and width < 150.0:
            return original
        return candidate


def _absorb_equations_into_callouts(
    callouts: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Treat a bordered callout as an atomic layout container.

    PDF text extraction often returns the prose and an inline/displayed equation
    inside the same red box as separate regions. The equation must not become a
    second floating object. We attach a nearby equation group to the callout,
    enlarge the frame to include it and remove the group from normal page flow.
    """
    remaining: list[dict[str, Any]] = []
    absorbed = 0
    for group in groups:
        if group.get("kind") != "equation":
            remaining.append(group)
            continue
        gb = list(map(float, group.get("bbox", [0, 0, 0, 0])))
        best: tuple[float, dict[str, Any]] | None = None
        for callout in callouts:
            cb = list(map(float, callout.get("bbox", [0, 0, 0, 0])))
            x_overlap = _axis_overlap(cb[0], cb[2], gb[0], gb[2])
            min_width = max(1.0, min(cb[2] - cb[0], gb[2] - gb[0]))
            x_ratio = x_overlap / min_width
            vertical_gap = gb[1] - cb[3]
            contained = _overlap_ratio(gb, cb, denominator="a") >= 0.72
            directly_below = -2.5 <= vertical_gap <= 10.0 and x_ratio >= 0.55
            if not (contained or directly_below):
                continue
            score = 0.0 if contained else abs(vertical_gap) + (1.0 - x_ratio) * 10.0
            if best is None or score < best[0]:
                best = (score, callout)
        if best is None:
            remaining.append(group)
            continue
        callout = best[1]
        callout.setdefault("contained_visual_groups", []).append({
            "id": group.get("id"),
            "kind": group.get("kind"),
            "bbox": group.get("bbox"),
            "member_ids": list(group.get("member_ids", [])),
        })
        combined = _union([list(map(float, callout["bbox"])), gb])
        # A small bottom allowance captures the red border without colliding with
        # the following callout. Horizontal dimensions remain those of the box.
        callout["bbox"] = [
            float(callout["bbox"][0]),
            min(float(callout["bbox"][1]), combined[1]),
            float(callout["bbox"][2]),
            combined[3] + 2.0,
        ]
        group["contained_in_callout"] = callout.get("id")
        absorbed += 1
    return remaining, absorbed


def _crop_visual_group(page: dict[str, Any], group: dict[str, Any], work_dir: Path, crop_dir: Path) -> str:
    crop_dir.mkdir(parents=True, exist_ok=True)
    render_path = work_dir / page["render"]
    output = crop_dir / f"p{page['page']}-{group['id'][:80]}.png"
    with Image.open(render_path) as image:
        sx = image.width / float(page.get("width_pt") or 595.0)
        sy = image.height / float(page.get("height_pt") or 842.0)
        x0, y0, x1, y1 = map(float, group["bbox"])
        pad_pt = 4.0 if group["kind"] == "equation" else 6.0
        box = (
            max(0, round((x0 - pad_pt) * sx)),
            max(0, round((y0 - pad_pt) * sy)),
            min(image.width, round((x1 + pad_pt) * sx)),
            min(image.height, round((y1 + pad_pt) * sy)),
        )
        image.crop(box).save(output)
    return str(output)




def _merge_groups_by_mathpix_coordinates(
    groups: list[dict[str, Any]],
    page: dict[str, Any],
    catalog: list[Any],
) -> list[dict[str, Any]]:
    """Make the Mathpix coordinate manifest the primary visual-object map.

    Each downloaded Mathpix image name records its source page and crop rectangle.
    PDF exporters often split that same object into tiny arrow bitmaps, labels and
    vector strokes, so asking a pre-merged PDF group to resemble the complete image
    misses many real assets (notably the equal-width exercise pages).  This pass is
    asset-centric: every positioned Mathpix occurrence becomes one visual group at
    its recorded rectangle, nearby PDF fragments are absorbed, and only the
    remaining unmatched PDF groups continue to the crop fallback.
    """
    if not catalog:
        return groups
    page_number = int(page.get("page") or 0)
    page_width = float(page.get("width_pt") or 595.0)
    page_height = float(page.get("height_pt") or 842.0)
    positioned = [
        record for record in catalog
        if record.coordinate_page == page_number and record.coordinate_bbox_px is not None
    ]
    if not positioned:
        return groups

    def asset_box(record: Any) -> list[float]:
        # Mathpix's crop coordinates are measured on a 2048-pixel-wide page
        # canvas. The same uniform scale maps x/y/width/height to PDF points.
        scale = page_width / 2048.0
        x0, y0, x1, y1 = record.coordinate_bbox_px
        return [
            round(max(0.0, x0 * scale), 3),
            round(max(0.0, y0 * scale), 3),
            round(min(page_width, x1 * scale), 3),
            round(min(page_height, y1 * scale), 3),
        ]

    assets = [(record, asset_box(record)) for record in positioned]

    # Work at raw region level rather than at the already-merged group level.
    # One PDF group can accidentally span two answer-option diagrams, while the
    # Mathpix manifest correctly records them as two independent images.
    raw_candidates: list[dict[str, Any]] = []
    for region in page.get("regions", []):
        if region.get("type") == "image" and _area(region.get("bbox", [0, 0, 0, 0])) >= 8.0:
            raw_candidates.append({"id": region.get("id"), "kind": "image", "bbox": list(map(float, region.get("bbox", [0, 0, 0, 0])))})
        elif region.get("type") == "text":
            semantic_kind = region.get("semantic", {}).get("type")
            if semantic_kind in {"equation", "figure_label"}:
                raw_candidates.append({"id": region.get("id"), "kind": semantic_kind, "bbox": list(map(float, region.get("bbox", [0, 0, 0, 0])))})

    assigned: dict[tuple[Any, ...], list[dict[str, Any]]] = {record.occurrence_key: [] for record, _ in assets}
    assigned_ids: set[str] = set()
    for candidate in raw_candidates:
        box = candidate["bbox"]
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        best: tuple[float, Any] | None = None
        for record, abox in assets:
            expanded = [abox[0] - 5.0, abox[1] - 5.0, abox[2] + 5.0, abox[3] + 5.0]
            coverage = _overlap_ratio(box, abox, denominator="a")
            center_inside = expanded[0] <= cx <= expanded[2] and expanded[1] <= cy <= expanded[3]
            if coverage < 0.04 and not center_inside:
                continue
            acx = (abox[0] + abox[2]) / 2.0
            acy = (abox[1] + abox[3]) / 2.0
            diag = max(1.0, ((abox[2] - abox[0]) ** 2 + (abox[3] - abox[1]) ** 2) ** 0.5)
            center_distance = (((cx - acx) ** 2 + (cy - acy) ** 2) ** 0.5) / diag
            score = coverage * 1.35 + (0.45 if center_inside else 0.0) - center_distance * 0.18
            if best is None or score > best[0]:
                best = (score, record)
        if best is not None:
            record = best[1]
            assigned[record.occurrence_key].append(candidate)
            assigned_ids.add(str(candidate.get("id")))

    replacements: list[dict[str, Any]] = []
    asset_boxes: list[list[float]] = []
    for record, abox in assets:
        members = assigned.get(record.occurrence_key, [])
        asset_boxes.append(abox)
        safe_name = "".join(ch if ch.isalnum() else "-" for ch in record.path.stem)[-70:]
        replacements.append({
            "id": f"vg-mathpix-p{page_number}-{safe_name}",
            "kind": "figure",
            "bbox": abox,
            "member_ids": [str(member.get("id")) for member in members],
            "member_kinds": sorted({str(member.get("kind")) for member in members}),
            "mathpix_position_match": {
                "record": record,
                "match": "mathpix-page-coordinate-manifest",
                "confidence": 1.0,
                "score": 1.0,
                "assetBBoxPt": abox,
                "mathpixPage": page_number,
                "mathpixBBoxPx": list(record.coordinate_bbox_px or ()),
                "absorbedRegionCount": len(members),
            },
            "absorbed_visual_group_ids": [],
            "editable_overlay_status": "source-image-preserved; internal equation/text overlays not reconstructed in this checkpoint",
        })

    # Remove any old group containing a fragment assigned to a manifest asset.
    # Also remove a fragment-only group that is substantially contained by an
    # asset rectangle even when its PDF region id was filtered earlier.
    remaining: list[dict[str, Any]] = []
    for group in groups:
        member_ids = {str(mid) for mid in group.get("member_ids", [])}
        if member_ids & assigned_ids:
            continue
        gbox = list(map(float, group.get("bbox", [0, 0, 0, 0])))
        gcx = (gbox[0] + gbox[2]) / 2.0
        gcy = (gbox[1] + gbox[3]) / 2.0
        covered = False
        for abox in asset_boxes:
            expanded = [abox[0] - 4.0, abox[1] - 4.0, abox[2] + 4.0, abox[3] + 4.0]
            center_inside = expanded[0] <= gcx <= expanded[2] and expanded[1] <= gcy <= expanded[3]
            if center_inside and _overlap_ratio(gbox, abox, denominator="a") >= 0.04:
                covered = True
                break
        if not covered:
            remaining.append(group)

    output = remaining + replacements
    output.sort(key=lambda g: (float(g.get("bbox", [0, 0, 0, 0])[1]), float(g.get("bbox", [0, 0, 0, 0])[0])))
    return output

def build_page_structure(
    pdf_analysis: dict[str, Any],
    work_dir: Path,
    asset_dir: Path,
    reference_docx: Path | None = None,
    external_asset_paths: list[Path] | None = None,
    equation_donor_path: Path | None = None,
) -> dict[str, Any]:
    result_pages: list[dict[str, Any]] = []
    catalog = build_asset_catalog(reference_docx, external_asset_paths or [], work_dir / "asset_catalog") if reference_docx else []
    asset_counts = {"mathpix-external": 0, "mathpix-docx": 0, "pdf-embedded": 0, "pdf-page-crop": 0}
    vector_native_count = 0
    positioned_match_count = 0
    used_positioned_assets: set[tuple[Any, ...]] = set()
    equation_donors, equation_donor_records = _load_equation_donors(equation_donor_path)
    used_equation_donors: set[str] = set()
    recovered_equation_count = 0
    for page in pdf_analysis.get("pages", []):
        columns, layout_detection = _detect_columns(page)
        column = _main_column(page)
        if columns:
            column = {
                "x0": columns[0]["x0"], "x1": columns[1]["x1"],
                "y0": min(columns[0]["y0"], columns[1]["y0"]),
                "y1": max(columns[0]["y1"], columns[1]["y1"]),
            }
        main_regions = []
        callouts = []
        headers = []
        footers = []
        banners = []
        text_regions: list[dict[str, Any]] = []
        for original_region in page.get("regions", []):
            if original_region.get("type") != "text":
                continue
            split_regions = _split_region_for_columns(original_region, columns, float(page.get("width_pt") or 595.0)) if columns else [original_region]
            text_regions.extend(split_regions)
        for region in text_regions:
            sem = region.get("semantic", {})
            kind = sem.get("type", "body")
            if kind == "callout":
                callouts.append(region)
            elif kind == "header":
                headers.append(region)
            elif kind == "footer":
                footers.append(region)
            elif kind == "banner":
                banners.append(region)
            elif kind in {"body", "heading", "caption"} and sem.get("flow_zone") == "main":
                main_regions.append(region)

        visual_groups = _group_visual_candidates(page)
        page_equation_group_count = sum(1 for g in visual_groups if g.get("kind") == "equation")
        page_equation_donor_count = len(equation_donors.get(int(page.get("page") or 0), []))
        visual_groups, absorbed_callout_equations = _absorb_equations_into_callouts(callouts, visual_groups)
        visual_groups = _merge_groups_by_mathpix_coordinates(visual_groups, page, catalog)
        col_box = [column["x0"], 0.0, column["x1"], float(page.get("height_pt", 842.0))]
        for group in visual_groups:
            if group.get("kind") == "equation":
                group["source_bbox"] = list(group["bbox"])
                group["bbox"] = _refine_equation_bbox_from_raster(page, group, column, work_dir)
                page_no = int(page.get("page") or 0)
                pdf_text = group.get("text", "") or " ".join(str(r.get("text", "")) for r in page.get("regions", []) if r.get("id") in set(group.get("member_ids", [])))
                pdf_sig = _equation_signature(pdf_text)
                best = None
                from rapidfuzz import fuzz
                for donor in equation_donors.get(page_no, []):
                    if donor.get("id") in used_equation_donors:
                        continue
                    donor_sig = str(donor.get("signature") or "")
                    score = fuzz.ratio(pdf_sig, donor_sig) if pdf_sig and donor_sig else 0
                    if best is None or score > best[0]:
                        best = (score, donor)
                chosen = best if best and best[0] >= 68 else None
                match_mode = "text-signature"
                if chosen is None and page_equation_group_count == 1 and page_equation_donor_count == 1:
                    only = equation_donors.get(page_no, [None])[0]
                    if only and only.get("id") not in used_equation_donors:
                        chosen = (55.0, only)
                        match_mode = "single-equation-page-order"
                if chosen:
                    donor = chosen[1]
                    group["markdown_latex"] = donor.get("latex")
                    group["markdown_equation_donor"] = {"id": donor.get("id"), "score": round(float(chosen[0]), 2), "matchMode": match_mode, "source": donor.get("source"), "pageConfidence": donor.get("pageConfidence")}
                    used_equation_donors.add(str(donor.get("id")))
                    recovered_equation_count += 1
            overlap = _overlap_ratio(group["bbox"], col_box, denominator="a")
            group["placement"] = "inline" if overlap >= 0.72 and group["bbox"][2] - group["bbox"][0] <= (column["x1"] - column["x0"]) * 1.08 else "floating"
            # Wide top artwork (chapter banner) must remain a floating page object.
            if group["bbox"][1] < column["y0"] and group["kind"] == "figure":
                group["placement"] = "floating"
            group["wrap"] = "square" if group["placement"] == "floating" and overlap >= 0.08 and group["bbox"][1] >= column["y0"] else "none"
            if group["placement"] == "inline":
                group["bbox"] = [
                    max(float(group["bbox"][0]), float(column["x0"])),
                    float(group["bbox"][1]),
                    min(float(group["bbox"][2]), float(column["x1"])),
                    float(group["bbox"][3]),
                ]
            # Fidelity asset policy:
            # 1) Mathpix page-coordinate match from downloaded asset filename,
            # 2) exact/perceptual match for a simple embedded PDF image,
            # 3) whole-group perceptual match,
            # 4) PDF crop only as final fallback.
            selected_path = None
            selected_svg = None
            position_match = group.get("mathpix_position_match")
            if position_match:
                record = position_match["record"]
                if record.occurrence_key not in used_positioned_assets:
                    materialized = materialize_asset(position_match, asset_dir, group["id"] + "_position")
                    selected_path = materialized["raster"]
                    selected_svg = materialized.get("svg")
                    used_positioned_assets.add(record.occurrence_key)
                    positioned_match_count += 1
                    group["asset_source"] = record.source
                    group["asset_match"] = {k: v for k, v in position_match.items() if k != "record"}
                    group["asset_original"] = str(record.path)
            if selected_path is None and group.get("member_kinds") == ["image"] and len(group.get("member_ids", [])) == 1:
                member_id = group["member_ids"][0]
                source_region = next((r for r in page.get("regions", []) if r.get("id") == member_id), None)
                relative_path = source_region.get("path") if source_region else None
                pdf_image_path = work_dir / relative_path if relative_path else None
                pixel_width = int(source_region.get("width") or 0) if source_region else 0
                pixel_height = int(source_region.get("height") or 0) if source_region else 0
                substantive = min(pixel_width, pixel_height) >= 48 and pixel_width * pixel_height >= 5000
                match = match_image_asset(pdf_image_path, catalog) if pdf_image_path and substantive else None
                if match:
                    materialized = materialize_asset(match, asset_dir, group["id"])
                    selected_path = materialized["raster"]
                    selected_svg = materialized.get("svg")
                    record = match["record"]
                    group["asset_source"] = record.source
                    group["asset_match"] = {k: v for k, v in match.items() if k != "record"}
                    group["asset_original"] = str(record.path)
                elif pdf_image_path and pdf_image_path.exists():
                    selected_path = asset_dir / f"{group['id'][:72]}_pdf.{pdf_image_path.suffix.lstrip('.') or 'png'}"
                    selected_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(pdf_image_path, selected_path)
                    group["asset_source"] = "pdf-embedded"
                    group["asset_match"] = {"match": "direct-pdf-image", "confidence": 1.0}
            if selected_path is None:
                page_crop = Path(_crop_visual_group(page, group, work_dir, asset_dir))
                whole_group_match = None
                if group.get("kind") == "figure" and "equation" not in set(group.get("member_kinds", [])):
                    whole_group_match = match_image_asset(page_crop, catalog)
                if whole_group_match:
                    materialized = materialize_asset(whole_group_match, asset_dir, group["id"] + "_group")
                    selected_path = materialized["raster"]
                    selected_svg = materialized.get("svg")
                    record = whole_group_match["record"]
                    group["asset_source"] = record.source
                    group["asset_match"] = {k: v for k, v in whole_group_match.items() if k != "record"}
                    group["asset_match"]["query"] = "whole-visual-group"
                    group["asset_original"] = str(record.path)
                else:
                    selected_path = page_crop
                    group["asset_source"] = "pdf-page-crop"
                    group["asset_match"] = {"match": "composite-fallback", "confidence": 1.0}
            if selected_svg:
                group["svg_path"] = str(selected_svg)
                group["native_vector"] = True
                vector_native_count += 1
            group.pop("mathpix_position_match", None)
            group["crop_path"] = str(selected_path)
            asset_counts[group["asset_source"]] = asset_counts.get(group["asset_source"], 0) + 1

        flow = _merge_flow_regions(main_regions)
        if columns:
            gutter_center = (float(columns[0]["x1"]) + float(columns[1]["x0"])) / 2.0
            for item in flow:
                box = list(map(float, item.get("bbox", [0,0,0,0])))
                role = item.get("column_role")
                if role in {"left", "right"}:
                    item["column_index"] = 0 if role == "left" else 1
                    item["spanning"] = False
                elif role == "span":
                    item["column_index"] = None
                    item["spanning"] = True
                elif box[0] < float(columns[0]["x1"]) and box[2] > float(columns[1]["x0"]):
                    item["column_index"] = None
                    item["spanning"] = True
                else:
                    item["column_index"] = 0 if (box[0] + box[2]) / 2.0 < gutter_center else 1
                    item["spanning"] = False
        inline_groups = [g for g in visual_groups if g["placement"] == "inline"]
        region_map = {r.get("id"): r for r in page.get("regions", [])}
        for group in inline_groups:
            member_text = " ".join(
                str(region_map.get(member_id, {}).get("text", "")).strip()
                for member_id in group.get("member_ids", [])
                if str(region_map.get(member_id, {}).get("text", "")).strip()
            )
            visual_item = {
                "id": "flow-" + group["id"],
                "type": "visual",
                "semantic_type": group["kind"],
                "bbox": group["bbox"],
                "visual_group_id": group["id"],
                "crop_path": group["crop_path"],
                "svg_path": group.get("svg_path"),
                "native_vector": bool(group.get("native_vector")),
                "asset_source": group.get("asset_source"),
                "text": member_text,
                "markdown_latex": group.get("markdown_latex"),
                "markdown_equation_donor": group.get("markdown_equation_donor"),
            }
            if columns:
                box = list(map(float, group.get("bbox", [0,0,0,0])))
                gutter_center = (float(columns[0]["x1"]) + float(columns[1]["x0"])) / 2.0
                visual_item["column_index"] = 0 if (box[0] + box[2]) / 2.0 < gutter_center else 1
                visual_item["spanning"] = False
            flow.append(visual_item)
        if columns:
            # Native Word columns consume the left column top-to-bottom, then the
            # right column. Spanning items are positioned separately by the builder.
            flow.sort(key=lambda x: (2 if x.get("column_index") is None else int(x.get("column_index", 0)), float(x["bbox"][1]), float(x["bbox"][0])))
            for ci in (0, 1):
                col_items = [item for item in flow if item.get("column_index") == ci and not item.get("spanning")]
                if col_items:
                    columns[ci]["y0"] = round(min(float(item["bbox"][1]) for item in col_items), 3)
                    columns[ci]["y1"] = round(max(float(item["bbox"][3]) for item in col_items), 3)
            column["y0"] = round(min(float(c["y0"]) for c in columns), 3)
            column["y1"] = round(max(float(c["y1"]) for c in columns), 3)
        else:
            flow.sort(key=lambda x: (float(x["bbox"][1]), float(x["bbox"][0])))

        result_pages.append({
            "page": page["page"],
            "width_pt": page["width_pt"],
            "height_pt": page["height_pt"],
            "page_fullness": page.get("page_fullness"),
            "main_column": column,
            "layout_mode": "two_columns" if columns else "single_column",
            "layout_detection": layout_detection,
            "columns": columns,
            "flow": flow,
            "callouts": [{
                "id": r["id"],
                "bbox": r["bbox"],
                "text": r["text"],
                "semantic": r.get("semantic", {}),
                "contained_visual_groups": list(r.get("contained_visual_groups", [])),
            } for r in callouts],
            "absorbed_callout_equation_count": absorbed_callout_equations,
            "headers": [{"id": r["id"], "bbox": r["bbox"], "text": r["text"]} for r in headers],
            "footers": [{"id": r["id"], "bbox": r["bbox"], "text": r["text"]} for r in footers],
            "banners": [{"id": r["id"], "bbox": r["bbox"], "text": r["text"]} for r in banners],
            "visual_groups": visual_groups,
        })
    ordered_trail_count = _attach_ordered_equation_donor_trail(
        result_pages,
        equation_donor_records,
        used_equation_donors,
    )
    recovered_equation_count += ordered_trail_count
    markdown_equation_map = _build_markdown_equation_map(result_pages, equation_donor_records)
    return {
        "pages": result_pages,
        "markdown_equation_map": markdown_equation_map,
        "asset_resolution": {
            "catalogSize": len(catalog),
            "sourceCounts": asset_counts,
            "positionedMathpixMatches": positioned_match_count,
            "nativeSvgSelections": vector_native_count,
            "markdownEquationDonorTrailMatches": ordered_trail_count,
            "markdownEquationRecoveredCount": recovered_equation_count,
            "policy": "Mathpix page-coordinate asset first; exact/perceptual raster second; native SVG paired with PNG fallback; PDF crop only for unresolved groups",
        },
    }
