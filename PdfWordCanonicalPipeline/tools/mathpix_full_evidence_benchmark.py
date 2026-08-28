from __future__ import annotations

import argparse
import json
import re
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from rapidfuzz import fuzz

from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map
from pdf_word_reconstructor.common import normalize_text
from pdf_word_reconstructor.docx_analyzer import analyze_docx
from pdf_word_reconstructor.docx_donor_map import _donor_type, _match_score, _prepare_markdown_record


VERSION = "mathpix-full-evidence-benchmark-0.1"
ACCEPTED = {"exact", "strong", "usable"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compare the complete Mathpix evidence for one isolated job: isolated MMD/DOCX versus full-job MMD/Lines."
    )
    p.add_argument("--package-root", required=True, type=Path, help="Folder containing 17-22.docx, 17-22.zip and 17-22-lines/")
    p.add_argument("--output", required=True, type=Path)
    return p


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_isolated_mmd(source_zip: Path, target: Path) -> Path:
    with zipfile.ZipFile(source_zip) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".mmd")]
        if not names:
            raise RuntimeError(f"No MMD in {source_zip}")
        target.write_bytes(zf.read(names[0]))
    return target


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


def _math_signature(text: str) -> str:
    text = str(text or "").casefold()
    text = re.sub(r"\\(?:mathrm|text|operatorname|mathbf|boldsymbol|left|right|displaystyle)", "", text)
    text = text.replace("−", "-").replace("–", "-").replace("—", "-").replace("⋅", "*").replace("·", "*").replace("×", "*")
    return re.sub(r"[^\w+\-*/=<>]", "", text, flags=re.UNICODE)


def _text_score(a: str, b: str, kind: str = "") -> float:
    if kind in {"display_equation", "equation", "math"}:
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
    containment = 94.0 if len(shorter) >= 12 and shorter in longer else 0.0
    length_ratio = min(len(a2), len(b2)) / max(1, max(len(a2), len(b2)))
    return min(100.0, max(containment, 0.36 * ratio + 0.34 * partial + 0.30 * token) * (0.80 + 0.20 * length_ratio))


def _status(score: float, margin: float = 999.0) -> str:
    if score >= 97.0 and margin >= 3.0:
        return "exact"
    if score >= 88.0 and margin >= 4.0:
        return "strong"
    if score >= 74.0 and margin >= 4.0:
        return "usable"
    if score >= 62.0:
        return "ambiguous"
    return "unmatched"


def _compatible(a: str, b: str) -> bool:
    a, b = str(a or ""), str(b or "")
    images = {"image", "figure", "diagram"}
    maths = {"display_equation", "equation", "math"}
    headings = {"heading", "title", "section_header"}
    if a in images or b in images:
        return a in images and b in images
    if a in maths or b in maths:
        return a in maths and b in maths
    if a in headings or b in headings:
        return a in headings and b in headings
    return True


def _sequence_align(source: list[dict[str, Any]], target: list[dict[str, Any]], score_fn) -> list[dict[str, Any]]:
    m, n = len(source), len(target)
    neg = -10**12
    dp = [[neg] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[str, int, int] | None]] = [[None] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = 0.0
    for i in range(1, m + 1):
        dp[i][0] = dp[i - 1][0] - 8.0
        back[i][0] = ("skip-source", i - 1, 0)
    for j in range(1, n + 1):
        dp[0][j] = dp[0][j - 1] - 1.5
        back[0][j] = ("skip-target", 0, j - 1)
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            best = dp[i - 1][j] - 8.0
            action = ("skip-source", i - 1, j)
            if dp[i][j - 1] - 1.5 > best:
                best = dp[i][j - 1] - 1.5
                action = ("skip-target", i, j - 1)
            score = float(score_fn(source[i - 1], target[j - 1]))
            if score >= 50.0:
                candidate = dp[i - 1][j - 1] + (score - 49.0)
                if candidate > best:
                    best = candidate
                    action = ("match", i - 1, j - 1)
            dp[i][j] = best
            back[i][j] = action
    i, j = m, n
    pairs: dict[int, tuple[int, float]] = {}
    while i or j:
        act = back[i][j]
        if act is None:
            break
        op, pi, pj = act
        if op == "match":
            score = float(score_fn(source[pi], target[pj]))
            pairs[pi] = (pj, score)
        i, j = pi, pj
    result = []
    for si, row in enumerate(source):
        pair = pairs.get(si)
        if pair is None:
            result.append({"sourceIndex": si, "targetIndex": None, "score": 0.0, "status": "unmatched"})
            continue
        ti, score = pair
        result.append({"sourceIndex": si, "targetIndex": ti, "score": round(score, 2), "status": _status(score)})
    return result


def _image_geometry_from_target(target: str) -> dict[str, float] | None:
    target = str(target or "")
    if not target:
        return None
    if target.startswith("http://") or target.startswith("https://"):
        query = parse_qs(urlparse(target).query)
        try:
            return {
                "x": float(query["top_left_x"][0]), "y": float(query["top_left_y"][0]),
                "w": float(query["width"][0]), "h": float(query["height"][0]),
            }
        except Exception:
            pass
    name = Path(target.replace("\\", "/")).name
    match = re.search(r"-(\d+)_([0-9]+)_([0-9]+)_([0-9]+)_([0-9]+)\.[A-Za-z]+$", name)
    if not match:
        return None
    _page, h, w, y, x = match.groups()
    return {"x": float(x), "y": float(y), "w": float(w), "h": float(h)}


def _image_page_from_target(target: str) -> int | None:
    name = Path(str(target or "").replace("\\", "/")).name
    match = re.search(r"-(\d+)_\d+_\d+_\d+_\d+\.[A-Za-z]+$", name)
    return int(match.group(1)) if match else None


def _geom_score(a: dict[str, float] | None, b: dict[str, float] | None) -> float:
    if not a or not b:
        return 0.0
    deltas = [abs(a[k] - b[k]) for k in ("x", "y", "w", "h")]
    scale = max(1.0, a["w"], a["h"], b["w"], b["h"])
    norm = sum(deltas) / (4.0 * scale)
    return max(0.0, 100.0 * (1.0 - norm * 4.0))


def _lines_semantic_units(lines_data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    units: list[dict[str, Any]] = []
    line_types: Counter[str] = Counter()
    fields: Counter[str] = Counter()
    total = hierarchy = geometry = confidence = 0

    for page in lines_data.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        rows = list(page.get("lines", []) or [])
        by_id = {str(row.get("id")): row for row in rows if row.get("id")}
        for row in rows:
            total += 1
            line_types[str(row.get("type") or "unknown")] += 1
            fields.update(row.keys())
            hierarchy += int(bool(row.get("parent_id") or row.get("children_ids")))
            geometry += int(isinstance(row.get("region"), dict) and bool(row.get("cnt")))
            confidence += int(row.get("confidence") is not None)

        def descendants(row: dict[str, Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for child_id in row.get("children_ids", []) or []:
                child = by_id.get(str(child_id))
                if child is None:
                    continue
                if child.get("children_ids"):
                    out.extend(descendants(child))
                else:
                    out.append(child)
            return out

        consumed: set[str] = set()
        # Explicit semantic containers first: retain them instead of discarding them.
        for row in rows:
            kind = str(row.get("type") or "")
            if kind not in {"section_header", "multiple_choice_block"}:
                continue
            leaves = descendants(row)
            text = "".join(str(leaf.get("text_display") or leaf.get("text") or "") for leaf in leaves).strip()
            units.append({
                "page": page_no, "kind": kind, "id": row.get("id"), "text": text,
                "region": row.get("region"), "cnt": row.get("cnt"),
                "childIds": [leaf.get("id") for leaf in leaves],
                "fontSize": row.get("font_size"), "column": row.get("column"),
            })
            consumed.update(str(leaf.get("id")) for leaf in leaves if leaf.get("id"))

        # Paragraph-like line groups using Mathpix continuation semantics.
        current: list[dict[str, Any]] = []
        def flush() -> None:
            nonlocal current
            if not current:
                return
            first = current[0]
            text = ""
            for row in current:
                piece = str(row.get("text") or "")
                subtype = str(row.get("subtype") or "")
                if not text:
                    text = piece
                elif subtype == "continues_line_no_hyphen":
                    text = text.rstrip("-") + piece.lstrip()
                elif subtype in {"continues_line_space", "continues_line_newline"}:
                    text += ("\n" if subtype == "continues_line_newline" else " ") + piece.lstrip()
                else:
                    text += " " + piece.lstrip()
            boxes = [r.get("region") for r in current if isinstance(r.get("region"), dict)]
            region = None
            if boxes:
                x0 = min(float(b.get("top_left_x") or 0) for b in boxes)
                y0 = min(float(b.get("top_left_y") or 0) for b in boxes)
                x1 = max(float(b.get("top_left_x") or 0) + float(b.get("width") or 0) for b in boxes)
                y1 = max(float(b.get("top_left_y") or 0) + float(b.get("height") or 0) for b in boxes)
                region = {"top_left_x": x0, "top_left_y": y0, "width": x1 - x0, "height": y1 - y0}
            units.append({
                "page": page_no, "kind": str(first.get("type") or "text"), "id": first.get("id"),
                "text": text.strip(), "region": region, "cnt": None,
                "childIds": [r.get("id") for r in current], "fontSize": first.get("font_size"),
                "column": first.get("column"), "parentId": first.get("parent_id"),
            })
            current = []

        for row in rows:
            rid = str(row.get("id") or "")
            kind = str(row.get("type") or "")
            if rid in consumed or kind in {"column", "page_info", "section_header", "multiple_choice_block"}:
                continue
            if kind in {"diagram", "math", "figure_label"}:
                flush()
                units.append({
                    "page": page_no, "kind": kind, "id": row.get("id"),
                    "text": str(row.get("text_display") or row.get("text") or "").strip(),
                    "region": row.get("region"), "cnt": row.get("cnt"), "childIds": [],
                    "fontSize": row.get("font_size"), "column": row.get("column"), "parentId": row.get("parent_id"),
                })
                continue
            subtype = str(row.get("subtype") or "")
            if current and not subtype.startswith("continues_line"):
                flush()
            current.append(row)
        flush()

    summary = {
        "pageCount": len(lines_data.get("pages", []) or []), "rawLineObjectCount": total,
        "lineTypes": dict(line_types), "observedFields": sorted(fields),
        "hierarchyCoverage": round(hierarchy / max(1, total), 5),
        "geometryCoverage": round(geometry / max(1, total), 5),
        "confidenceCoverage": round(confidence / max(1, total), 5),
        "semanticUnitCount": len(units), "semanticUnitTypes": dict(Counter(str(u.get("kind")) for u in units)),
    }
    return units, summary


def _lines_score(md: dict[str, Any], unit: dict[str, Any]) -> float:
    kind = str(md.get("type") or "")
    unit_kind = str(unit.get("kind") or "")
    if not _compatible(kind, unit_kind):
        return 0.0
    if kind in {"image", "figure"}:
        target = str(md.get("target") or "")
        auth = md.get("authoritativeContent") if isinstance(md.get("authoritativeContent"), dict) else {}
        if not target:
            targets = auth.get("imageTargets") or []
            target = str(targets[0] if targets else auth.get("target") or "")
        geom = _image_geometry_from_target(target)
        region = unit.get("region") if isinstance(unit.get("region"), dict) else None
        other = None
        if region:
            other = {"x": float(region.get("top_left_x") or 0), "y": float(region.get("top_left_y") or 0), "w": float(region.get("width") or 0), "h": float(region.get("height") or 0)}
        return _geom_score(geom, other)
    return _text_score(_record_text(md), str(unit.get("text") or ""), kind)


def _mmd_score(a: dict[str, Any], b: dict[str, Any]) -> float:
    if not _compatible(str(a.get("type") or ""), str(b.get("type") or "")):
        return 0.0
    if str(a.get("type") or "") in {"image", "figure"}:
        def target(record: dict[str, Any]) -> str:
            auth = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
            values = auth.get("imageTargets") or []
            return str(record.get("target") or (values[0] if values else auth.get("target") or ""))
        return _geom_score(_image_geometry_from_target(target(a)), _image_geometry_from_target(target(b)))
    return _text_score(_record_text(a), _record_text(b), str(a.get("type") or ""))


def _docx_matches(markdown: list[dict[str, Any]], docx_path: Path) -> list[dict[str, Any]]:
    analysis = analyze_docx(docx_path)
    paras = []
    for paragraph in analysis.get("paragraphs", []) or []:
        row = dict(paragraph)
        row["normalizedText"] = normalize_text(str(paragraph.get("text") or ""))
        row["donorType"] = _donor_type(row)
        if row["normalizedText"] or int(row.get("omml_count") or 0) or int(row.get("drawing_count") or 0) or row.get("numbering"):
            paras.append(row)
    result = []
    for record in markdown:
        prepared = _prepare_markdown_record(record)
        ranked = sorted(((float(_match_score(prepared, p)), p) for p in paras), key=lambda x: x[0], reverse=True)[:2]
        best = ranked[0][0] if ranked else 0.0
        second = ranked[1][0] if len(ranked) > 1 else 0.0
        result.append({
            "score": round(best, 2), "status": _status(best, best - second),
            "paragraphId": ranked[0][1].get("id") if ranked else None,
            "paragraphIndex": ranked[0][1].get("index") if ranked else None,
        })
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.package_root
    isolated_zip = root / "17-22.zip"
    isolated_docx = root / "17-22.docx"
    lines_dir = root / "17-22-lines"
    full_mmd_path = lines_dir / "result.mmd"
    lines_path = lines_dir / "result.lines.json"
    manifest_path = lines_dir / "manifest.json"
    for path in (isolated_zip, isolated_docx, full_mmd_path, lines_path, manifest_path):
        if not path.exists():
            raise FileNotFoundError(path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mathpix-evidence-") as td:
        temp = Path(td)
        isolated_mmd = _extract_isolated_mmd(isolated_zip, temp / "isolated.mmd")
        full_mmd = temp / "full-job.mmd"
        full_mmd.write_bytes(full_mmd_path.read_bytes())
        isolated_map = extract_markdown_element_map([isolated_mmd], temp / "isolated-map.json")
        full_map = extract_markdown_element_map([full_mmd], temp / "full-map.json")

        isolated_records = list(isolated_map.get("records", []) or [])
        full_records = list(full_map.get("records", []) or [])
        mmd_matches = _sequence_align(isolated_records, full_records, _mmd_score)

        lines_data = _load_json(lines_path)
        line_units, lines_summary = _lines_semantic_units(lines_data)
        line_matches = _sequence_align(isolated_records, line_units, _lines_score)
        docx_matches = _docx_matches(isolated_records, isolated_docx)

        rows = []
        consensus: Counter[str] = Counter()
        for i, record in enumerate(isolated_records):
            mmd = mmd_matches[i]
            lin = line_matches[i]
            doc = docx_matches[i]
            witnesses = sum(int(x.get("status") in ACCEPTED) for x in (mmd, lin, doc))
            label = f"{witnesses}-of-3"
            consensus[label] += 1
            target_mmd = full_records[mmd["targetIndex"]] if mmd.get("targetIndex") is not None else None
            target_line = line_units[lin["targetIndex"]] if lin.get("targetIndex") is not None else None
            rows.append({
                "markdownId": record.get("id"), "type": record.get("type"), "orderIndex": record.get("orderIndex"),
                "textPreview": _record_text(record)[:220], "witnessCount": witnesses,
                "fullJobMmd": {**mmd, "targetId": target_mmd.get("id") if target_mmd else None, "targetType": target_mmd.get("type") if target_mmd else None},
                "docx": doc,
                "lines": {**lin, "unit": target_line},
            })

        manifest = _load_json(manifest_path)
        report = {
            "version": VERSION,
            "principle": "Use complete Mathpix outputs as independent witnesses; preserve Lines hierarchy, semantic containers and geometry before any reconstruction rule is introduced.",
            "inputs": {
                "packageRoot": str(root), "isolatedMmdRecordCount": len(isolated_records),
                "fullJobMmdRecordCount": len(full_records), "docx": str(isolated_docx),
                "fullJobFileId": manifest.get("file_id"), "fullJobRequestedPages": manifest.get("requested_pages"),
            },
            "linesInventory": lines_summary,
            "mmdToMmd": {
                "statusCounts": dict(Counter(x["status"] for x in mmd_matches)),
                "acceptedCount": sum(x["status"] in ACCEPTED for x in mmd_matches),
            },
            "markdownToDocx": {
                "statusCounts": dict(Counter(x["status"] for x in docx_matches)),
                "acceptedCount": sum(x["status"] in ACCEPTED for x in docx_matches),
            },
            "markdownToLines": {
                "statusCounts": dict(Counter(x["status"] for x in line_matches)),
                "acceptedCount": sum(x["status"] in ACCEPTED for x in line_matches),
            },
            "consensus": dict(consensus),
            "items": rows,
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nMATHPIX FULL EVIDENCE BENCHMARK")
    print(f"Isolated MMD records: {report['inputs']['isolatedMmdRecordCount']}")
    print(f"Full-job MMD records: {report['inputs']['fullJobMmdRecordCount']}")
    print(f"Lines raw objects: {report['linesInventory']['rawLineObjectCount']}")
    print(f"Lines semantic units: {report['linesInventory']['semanticUnitCount']}")
    print(f"Lines hierarchy coverage: {report['linesInventory']['hierarchyCoverage']}")
    print(f"Lines geometry coverage: {report['linesInventory']['geometryCoverage']}")
    print(f"Isolated MMD -> full-job MMD: {report['mmdToMmd']['statusCounts']}")
    print(f"Isolated MMD -> DOCX: {report['markdownToDocx']['statusCounts']}")
    print(f"Isolated MMD -> Lines: {report['markdownToLines']['statusCounts']}")
    print(f"Consensus: {report['consensus']}")
    weak = [row for row in report['items'] if int(row['witnessCount']) < 2]
    print(f"Items with fewer than 2 confirming witnesses: {len(weak)}")
    for row in weak[:30]:
        print(f"  {row['markdownId']} {row['type']} witnesses={row['witnessCount']} {row['textPreview'][:90]}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
