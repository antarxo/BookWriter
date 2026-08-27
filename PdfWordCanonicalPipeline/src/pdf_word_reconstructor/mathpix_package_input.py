from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


VERSION = "mathpix-package-input-0.1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_ASSET_NAME_RE = re.compile(
    r"^(?P<file_id>[0-9a-f-]+)-(?P<page>\d{3})_(?P<height>\d+)_(?P<width>\d+)_(?P<top_left_y>\d+)_(?P<top_left_x>\d+)\.(?P<ext>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first(package_dir: Path, name: str) -> Path | None:
    candidates = sorted(path for path in package_dir.rglob(name) if path.is_file())
    return candidates[0] if candidates else None


def _find_packaged_mmd(package_dir: Path) -> Path | None:
    candidates = sorted(path for path in package_dir.rglob("*.mmd") if path.is_file())
    if not candidates:
        return None
    # Prefer the unpacked mmd.zip representation because its image references
    # point at durable local package assets instead of temporary CDN crops.
    ranked = sorted(
        candidates,
        key=lambda path: (
            0 if "mmd_package" in {part.lower() for part in path.parts} else 1,
            0 if "__nested__" in {part.lower() for part in path.parts} else 1,
            len(path.parts),
            str(path),
        ),
    )
    return ranked[0]


def _extract_image_refs(markdown: str) -> list[str]:
    return [match.strip() for match in _IMAGE_REF_RE.findall(markdown or "") if match.strip()]


def _cdn_geometry(ref: str) -> dict[str, Any] | None:
    if not ref.lower().startswith(("http://", "https://")):
        return None
    parsed = urlparse(ref)
    query = parse_qs(parsed.query)
    try:
        page_match = re.search(r"-(\d{3})\.[A-Za-z0-9]+$", Path(parsed.path).name)
        page = int(page_match.group(1)) if page_match else None
        height = int((query.get("height") or [None])[0])
        width = int((query.get("width") or [None])[0])
        top_left_y = int((query.get("top_left_y") or [None])[0])
        top_left_x = int((query.get("top_left_x") or [None])[0])
    except (TypeError, ValueError):
        return None
    if page is None:
        return None
    return {
        "page": page,
        "height": height,
        "width": width,
        "top_left_y": top_left_y,
        "top_left_x": top_left_x,
    }


def _asset_geometry(path: Path) -> dict[str, Any] | None:
    match = _ASSET_NAME_RE.match(path.name)
    if not match:
        return None
    groups = match.groupdict()
    return {
        "file_id": groups["file_id"],
        "page": int(groups["page"]),
        "height": int(groups["height"]),
        "width": int(groups["width"]),
        "top_left_y": int(groups["top_left_y"]),
        "top_left_x": int(groups["top_left_x"]),
        "extension": groups["ext"].lower(),
    }


def _geometry_key(value: dict[str, Any] | None) -> tuple[int, int, int, int, int] | None:
    if not value:
        return None
    try:
        return (
            int(value["page"]),
            int(value["height"]),
            int(value["width"]),
            int(value["top_left_y"]),
            int(value["top_left_x"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _select_asset_root(package_dir: Path, packaged_mmd: Path | None) -> Path | None:
    if packaged_mmd is not None:
        candidate = packaged_mmd.parent / "images"
        if candidate.is_dir():
            return candidate
    candidates = sorted(path for path in package_dir.rglob("images") if path.is_dir())
    for candidate in candidates:
        if any(path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS for path in candidate.rglob("*")):
            return candidate
    return None


def build_mathpix_package_map(package_dir: Path, lines_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a lossless package-level inventory around Mathpix MMD + lines.

    This map does not reconstruct Word content. It preserves source/package
    metadata, both Markdown reference forms, every packaged visual asset, and
    page-scoped relationships so later structure adapters can consume evidence
    without rediscovering or discarding package information.
    """
    package_dir = Path(package_dir)
    manifest_path = _first(package_dir, "manifest.json")
    status_path = _first(package_dir, "status.json")
    lines_path = _first(package_dir, "result.lines.json") or _first(package_dir, "lines.json")
    canonical_mmd_path = _first(package_dir, "result.mmd")
    packaged_mmd_path = _find_packaged_mmd(package_dir)
    mmd_zip_path = _first(package_dir, "result.mmd.zip")

    manifest = _read_json(manifest_path)
    status = _read_json(status_path)
    canonical_mmd = canonical_mmd_path.read_text(encoding="utf-8") if canonical_mmd_path else ""
    packaged_mmd = packaged_mmd_path.read_text(encoding="utf-8") if packaged_mmd_path else ""
    canonical_refs = _extract_image_refs(canonical_mmd)
    packaged_refs = _extract_image_refs(packaged_mmd)

    asset_root = _select_asset_root(package_dir, packaged_mmd_path)
    asset_paths = sorted(
        path for path in (asset_root.rglob("*") if asset_root else [])
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )

    canonical_by_geometry: dict[tuple[int, int, int, int, int], list[str]] = defaultdict(list)
    for ref in canonical_refs:
        key = _geometry_key(_cdn_geometry(ref))
        if key is not None:
            canonical_by_geometry[key].append(ref)

    packaged_ref_names = {
        Path(ref.replace("\\", "/")).name
        for ref in packaged_refs
        if not ref.lower().startswith(("http://", "https://"))
    }

    line_objects_by_geometry: dict[tuple[int, int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for page in (lines_map or {}).get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for obj in page.get("objects", []) or []:
            box = obj.get("bbox_px") if isinstance(obj.get("bbox_px"), dict) else None
            if not box:
                continue
            try:
                key = (
                    page_no,
                    int(round(float(box.get("height") or 0))),
                    int(round(float(box.get("width") or 0))),
                    int(round(float(box.get("y0") or 0))),
                    int(round(float(box.get("x0") or 0))),
                )
            except (TypeError, ValueError):
                continue
            line_objects_by_geometry[key].append({
                "id": obj.get("id"),
                "type": obj.get("type"),
                "subtype": obj.get("subtype"),
                "conversion_output": obj.get("conversion_output"),
            })

    assets: list[dict[str, Any]] = []
    page_assets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    matched_canonical_refs: set[str] = set()
    matched_packaged_refs: set[str] = set()
    for index, path in enumerate(asset_paths, start=1):
        geometry = _asset_geometry(path)
        key = _geometry_key(geometry)
        canonical_matches = list(canonical_by_geometry.get(key, [])) if key else []
        local_ref_matches = sorted(
            ref for ref in packaged_refs
            if Path(ref.replace("\\", "/")).name == path.name
        )
        matched_canonical_refs.update(canonical_matches)
        matched_packaged_refs.update(local_ref_matches)
        related_lines = list(line_objects_by_geometry.get(key, [])) if key else []
        record = {
            "id": f"mathpix-asset-{index:04d}",
            "filename": path.name,
            "path": str(path),
            "relativePath": str(path.relative_to(package_dir)) if path.is_relative_to(package_dir) else path.name,
            "sha256": _sha256(path),
            "sizeBytes": path.stat().st_size,
            "geometry": geometry,
            "canonicalMmdReferenced": bool(canonical_matches),
            "packagedMmdReferenced": bool(local_ref_matches),
            "canonicalMmdReferences": canonical_matches,
            "packagedMmdReferences": local_ref_matches,
            "relatedLineObjects": related_lines,
        }
        assets.append(record)
        if geometry and int(geometry.get("page") or 0) > 0:
            page_assets[int(geometry["page"])].append(record)

    canonical_unmatched = [ref for ref in canonical_refs if ref not in matched_canonical_refs]
    packaged_unmatched = [ref for ref in packaged_refs if ref not in matched_packaged_refs]
    unreferenced_assets = [asset for asset in assets if not asset["packagedMmdReferenced"]]

    manifest_image_count = (((manifest or {}).get("mmd_package") or {}).get("image_count"))
    status_page_count = (status or {}).get("num_pages")
    line_page_count = ((lines_map or {}).get("summary") or {}).get("pageCount")

    audit = {
        "status": "complete" if not packaged_unmatched else "incomplete",
        "assetCount": len(assets),
        "canonicalMmdImageReferenceCount": len(canonical_refs),
        "canonicalMmdUniqueImageReferenceCount": len(set(canonical_refs)),
        "packagedMmdImageReferenceCount": len(packaged_refs),
        "packagedMmdUniqueImageReferenceCount": len(set(packaged_refs)),
        "packagedMmdResolvedReferenceCount": len(matched_packaged_refs),
        "packagedMmdUnresolvedReferences": packaged_unmatched,
        "canonicalMmdGeometryResolvedReferenceCount": len(matched_canonical_refs),
        "canonicalMmdGeometryUnresolvedReferences": canonical_unmatched,
        "unreferencedPackagedAssetCount": len(unreferenced_assets),
        "manifestImageCount": manifest_image_count,
        "manifestImageCountMatches": manifest_image_count in (None, len(assets)),
        "statusPageCount": status_page_count,
        "linesPageCount": line_page_count,
        "pageCountsAgree": status_page_count in (None, line_page_count) or line_page_count is None,
        "policy": "no packaged asset is discarded merely because canonical MMD does not reference it",
    }

    pages = [
        {
            "page": page_no,
            "assets": rows,
            "assetCount": len(rows),
            "canonicalMmdReferencedAssetCount": sum(1 for row in rows if row["canonicalMmdReferenced"]),
            "packagedMmdReferencedAssetCount": sum(1 for row in rows if row["packagedMmdReferenced"]),
        }
        for page_no, rows in sorted(page_assets.items())
    ]

    return {
        "version": VERSION,
        "packageDir": str(package_dir),
        "policy": "lossless package inventory first; reconstruction decisions belong to later adapters",
        "source": {
            "manifestPath": str(manifest_path) if manifest_path else None,
            "statusPath": str(status_path) if status_path else None,
            "linesPath": str(lines_path) if lines_path else None,
            "canonicalMmdPath": str(canonical_mmd_path) if canonical_mmd_path else None,
            "packagedMmdPath": str(packaged_mmd_path) if packaged_mmd_path else None,
            "mmdZipPath": str(mmd_zip_path) if mmd_zip_path else None,
            "manifest": manifest,
            "status": status,
        },
        "markdown": {
            "canonicalImageReferences": canonical_refs,
            "packagedImageReferences": packaged_refs,
            "canonicalReferenceCount": len(canonical_refs),
            "packagedReferenceCount": len(packaged_refs),
        },
        "assets": assets,
        "pages": pages,
        "audit": audit,
    }


def summarize_mathpix_package(package_map: dict[str, Any] | None) -> dict[str, Any]:
    if not package_map:
        return {"version": VERSION, "available": False}
    audit = package_map.get("audit") or {}
    source = package_map.get("source") or {}
    return {
        "version": VERSION,
        "available": True,
        "packageDir": package_map.get("packageDir"),
        "assetCount": audit.get("assetCount"),
        "canonicalMmdImageReferenceCount": audit.get("canonicalMmdImageReferenceCount"),
        "packagedMmdImageReferenceCount": audit.get("packagedMmdImageReferenceCount"),
        "packagedMmdResolvedReferenceCount": audit.get("packagedMmdResolvedReferenceCount"),
        "unreferencedPackagedAssetCount": audit.get("unreferencedPackagedAssetCount"),
        "packageAuditStatus": audit.get("status"),
        "manifestAvailable": bool(source.get("manifest")),
        "statusAvailable": bool(source.get("status")),
    }
