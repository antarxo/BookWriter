from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import parse_qs, urlparse

from pdf_word_canonical_pipeline.markdown_element_map_v03 import extract_markdown_element_map
from pdf_word_reconstructor.lines_page_frame_visual import build_page_frame_visual

VERSION = "lines-page-evidence-model-0.2"


def _bbox_iou(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    ix0 = max(a[0], b[0]); iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2]); iy1 = min(a[3], b[3])
    iw = max(0.0, ix1 - ix0); ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = aa + bb - inter
    return inter / union if union > 0 else 0.0


def _image_geometry_from_target(target: str) -> dict[str, float] | None:
    target = str(target or "")
    if not target:
        return None
    if target.startswith("http://") or target.startswith("https://"):
        query = parse_qs(urlparse(target).query)
        try:
            return {
                "x": float(query["top_left_x"][0]),
                "y": float(query["top_left_y"][0]),
                "w": float(query["width"][0]),
                "h": float(query["height"][0]),
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


def _target(record: dict[str, Any]) -> str:
    auth = record.get("authoritativeContent") if isinstance(record.get("authoritativeContent"), dict) else {}
    targets = auth.get("imageTargets") or []
    return str(record.get("target") or (targets[0] if targets else auth.get("target") or ""))


def _mmd_visuals(mmd_path: Path) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="page-evidence-") as td:
        mapped = extract_markdown_element_map([mmd_path], Path(td) / "map.json")
    out = []
    for rec in mapped.get("records", []) or []:
        if str(rec.get("type") or "") not in {"image", "figure"}:
            continue
        target = _target(rec)
        geom = _image_geometry_from_target(target)
        local_page = _image_page_from_target(target)
        bbox = None
        if geom:
            bbox = [geom["x"], geom["y"], geom["x"] + geom["w"], geom["y"] + geom["h"]]
        out.append({
            "id": rec.get("id"),
            "type": rec.get("type"),
            "localPage": local_page,
            "page": local_page,
            "target": target,
            "bboxPx": bbox,
            "geometryAvailable": bbox is not None,
        })
    return out


def _requested_page_sequence(manifest: dict[str, Any] | None, lines_pages: list[int]) -> list[int]:
    if isinstance(manifest, dict):
        raw = manifest.get("requested_pages")
        if isinstance(raw, list):
            vals = []
            for v in raw:
                try:
                    vals.append(int(v))
                except (TypeError, ValueError):
                    pass
            if vals:
                return vals
        if isinstance(raw, str):
            nums = [int(x) for x in re.findall(r"\d+", raw)]
            if len(nums) >= 2 and "-" in raw:
                a, b = nums[0], nums[1]
                if b >= a:
                    return list(range(a, b + 1))
            if nums:
                return nums
    return sorted(lines_pages)


def _map_package_pages(mmd_visuals: list[dict[str, Any]], manifest: dict[str, Any] | None, lines_pages: list[int]) -> dict[str, Any]:
    requested = _requested_page_sequence(manifest, lines_pages)
    unique_local = sorted({int(v["localPage"]) for v in mmd_visuals if isinstance(v.get("localPage"), int)})
    mapping: dict[int, int] = {}

    # Most Mathpix asset filenames use local package pages 1..N. Map those
    # positions onto manifest requested_pages / Lines source page numbers.
    if unique_local and requested:
        if min(unique_local) >= 1 and max(unique_local) <= len(requested):
            mapping = {lp: requested[lp - 1] for lp in unique_local}
        elif unique_local == requested:
            mapping = {lp: lp for lp in unique_local}

    for v in mmd_visuals:
        lp = v.get("localPage")
        if isinstance(lp, int) and lp in mapping:
            v["page"] = mapping[lp]

    return {
        "requestedPages": requested,
        "localPackagePages": unique_local,
        "localToSourcePage": {str(k): v for k, v in sorted(mapping.items())},
        "resolved": bool(mapping) or not unique_local,
    }


def _cross_page_template(pages: list[dict[str, Any]]) -> dict[str, Any]:
    envelopes = [p.get("activeContentEnvelope", {}).get("bboxPx") for p in pages]
    envelopes = [b for b in envelopes if b]
    if not envelopes:
        return {"candidate": None, "confidence": 0.0}
    lefts = [b[0] for b in envelopes]
    tops = [b[1] for b in envelopes]
    rights = [b[2] for b in envelopes]
    bottoms = [b[3] for b in envelopes]
    candidate = [median(lefts), median(tops), median(rights), median(bottoms)]
    deviations = []
    for b in envelopes:
        deviations.append(sum(abs(b[i] - candidate[i]) for i in range(4)) / 4.0)
    typical_dev = median(deviations) if deviations else 0.0
    page_w = median([p["physicalPage"]["widthPx"] for p in pages])
    page_h = median([p["physicalPage"]["heightPx"] for p in pages])
    confidence = max(0.0, min(0.92, 1.0 - typical_dev / max(1.0, 0.12 * min(page_w, page_h))))
    return {
        "candidateBBoxPx": [round(v, 2) for v in candidate],
        "confidence": round(confidence, 3),
        "medianDeviationPx": round(typical_dev, 2),
        "policy": "cross-page template candidate only; not yet a Word section margin",
    }


def build_page_evidence_model(lines_path: Path, mmd_path: Path | None = None, manifest_path: Path | None = None) -> dict[str, Any]:
    frame = build_page_frame_visual(lines_path)
    pages = []
    for p in frame.get("pages", []) or []:
        bf = p.get("bodyFrameCandidate") or {}
        pages.append({
            "page": p.get("page"),
            "physicalPage": p.get("physicalPage"),
            "activeContentEnvelope": {
                "bboxPx": bf.get("bboxPx"),
                "occupiedUnionBBoxPx": bf.get("occupiedUnionBBoxPx"),
                "edgeDistancesPx": bf.get("marginsPx"),
                "confidence": bf.get("confidence"),
                "policy": "occupied-content envelope; these distances are not Word margins",
            },
            "pageDecorationCandidates": p.get("pageDecorationCandidates") or [],
            "linesVisualEntities": p.get("visualEntities") or [],
            "occupancyGraphRef": p.get("occupancyGraphRef"),
        })

    manifest = None
    if manifest_path is not None and Path(manifest_path).exists():
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except Exception:
            manifest = None

    mmd_visuals: list[dict[str, Any]] = []
    if mmd_path is not None and Path(mmd_path).exists():
        mmd_visuals = _mmd_visuals(Path(mmd_path))

    lines_page_numbers = [int(p.get("page")) for p in pages if isinstance(p.get("page"), int)]
    page_mapping = _map_package_pages(mmd_visuals, manifest, lines_page_numbers)

    mmd_by_page: dict[int, list[dict[str, Any]]] = {}
    for v in mmd_visuals:
        if isinstance(v.get("page"), int):
            mmd_by_page.setdefault(int(v["page"]), []).append(v)

    total_lines = 0
    total_mmd = len(mmd_visuals)
    matched = 0
    missing_from_lines = 0
    unassigned_package = 0
    valid_pages = set(lines_page_numbers)
    for v in mmd_visuals:
        if v.get("page") not in valid_pages:
            unassigned_package += 1

    for p in pages:
        lines_visuals = p["linesVisualEntities"]
        total_lines += len(lines_visuals)
        package_visuals = mmd_by_page.get(int(p.get("page") or 0), [])
        audit = []
        used_lines: set[int] = set()
        for mv in package_visuals:
            best_i = None; best_iou = 0.0
            for i, lv in enumerate(lines_visuals):
                if i in used_lines:
                    continue
                score = _bbox_iou(mv.get("bboxPx"), lv.get("bboxPx"))
                if score > best_iou:
                    best_iou = score; best_i = i
            status = "package-visual-unmatched"
            lines_id = None
            if best_i is not None and best_iou >= 0.35:
                used_lines.add(best_i)
                status = "matched-lines-visual"
                lines_id = lines_visuals[best_i].get("id")
                matched += 1
            else:
                missing_from_lines += 1
            audit.append({
                "packageVisualId": mv.get("id"),
                "packageTarget": mv.get("target"),
                "packageLocalPage": mv.get("localPage"),
                "packageSourcePage": mv.get("page"),
                "packageBBoxPx": mv.get("bboxPx"),
                "linesVisualId": lines_id,
                "iou": round(best_iou, 4),
                "status": status,
            })
        for i, lv in enumerate(lines_visuals):
            if i not in used_lines:
                audit.append({
                    "packageVisualId": None,
                    "packageTarget": None,
                    "packageBBoxPx": None,
                    "linesVisualId": lv.get("id"),
                    "iou": 0.0,
                    "status": "lines-visual-without-mmd-match",
                })
        p["packageVisualEntities"] = package_visuals
        p["visualCompletenessAudit"] = audit

    template = _cross_page_template(pages)
    repeated_edge = frame.get("repeatedEdgeEvidence") or []
    return {
        "version": VERSION,
        "sources": {
            "lines": str(lines_path),
            "mmd": str(mmd_path) if mmd_path else None,
            "manifest": str(manifest_path) if manifest_path else None,
            "manifestFileId": manifest.get("file_id") if isinstance(manifest, dict) else None,
            "manifestRequestedPages": manifest.get("requested_pages") if isinstance(manifest, dict) else None,
        },
        "pageNumberMapping": page_mapping,
        "policy": (
            "Diagnostic page evidence model. Active-content envelope is not a Word margin. "
            "Header/footer/page-decoration labels remain candidates. Lines visuals and Mathpix package visuals are audited independently; actual asset insertion remains deferred. "
            "Mathpix package-local asset page numbers are mapped to source/Lines page numbers through manifest requested_pages when available."
        ),
        "summary": {
            "pageCount": len(pages),
            "linesVisualCount": total_lines,
            "packageVisualCount": total_mmd,
            "matchedVisualCount": matched,
            "packageVisualMissingFromLinesCount": missing_from_lines,
            "unassignedPackageVisualCount": unassigned_package,
            "repeatedEdgeSignatureCount": len(repeated_edge),
        },
        "crossPageTemplateCandidate": template,
        "repeatedEdgeEvidence": repeated_edge,
        "pages": pages,
    }


__all__ = ["build_page_evidence_model"]
