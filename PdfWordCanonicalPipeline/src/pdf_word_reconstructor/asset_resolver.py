from __future__ import annotations

import hashlib
import math
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from lxml import etree
from PIL import Image, ImageOps

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SVG_EXTENSION = ".svg"
_MATHPIX_GEOMETRY_RE = re.compile(r"-(\d{3})_(\d+)_(\d+)_(\d+)_(\d+)$")


@dataclass(frozen=True)
class AssetRecord:
    path: Path
    source: str
    sha256: str
    width: int
    height: int
    ahash: int
    thumbnail: bytes
    ink_ratio: float
    hash_bits: int = 256
    coordinate_page: int | None = None
    coordinate_bbox_px: tuple[int, int, int, int] | None = None
    vector_path: Path | None = None

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)

    @property
    def occurrence_key(self) -> tuple[Any, ...]:
        return (
            self.sha256,
            self.coordinate_page,
            self.coordinate_bbox_px,
            str(self.path),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_metrics(path: Path) -> tuple[int, int, int, bytes, float]:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("L")
        width, height = image.size
        normalized = ImageOps.autocontrast(image.resize((32, 32), Image.Resampling.LANCZOS))
        thumb_bytes = normalized.tobytes()
        small = normalized.resize((16, 16), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
        average = sum(pixels) / max(1, len(pixels))
        bits = 0
        for value in pixels:
            bits = (bits << 1) | int(value >= average)
        ink_ratio = sum(value < 235 for value in thumb_bytes) / max(1, len(thumb_bytes))
        return int(width), int(height), bits, thumb_bytes, float(ink_ratio)


def _safe_copy(source: Path, target_dir: Path, prefix: str) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    base = source.name.replace(" ", "_")
    target = target_dir / f"{prefix}_{base}"
    counter = 2
    source_sha = _sha256(source)
    while target.exists() and _sha256(target) != source_sha:
        target = target_dir / f"{prefix}_{counter}_{base}"
        counter += 1
    if not target.exists():
        shutil.copy2(source, target)
    return target


def _parse_mathpix_geometry(path: Path) -> tuple[int | None, tuple[int, int, int, int] | None]:
    """Parse Mathpix's page/geometry file name.

    Current all-formats exports use:
      ...-PPP_HEIGHT_WIDTH_Y_X.ext

    The coordinates are in a 2048-pixel-wide page canvas.  Keeping the raw
    values here lets the resolver map them to any PDF page size later.
    """
    match = _MATHPIX_GEOMETRY_RE.search(path.stem)
    if not match:
        return None, None
    page, height, width, y, x = map(int, match.groups())
    return page, (x, y, x + width, y + height)


def _extract_docx_media_and_svg_pairs(
    docx_path: Path,
    target_dir: Path,
) -> tuple[list[Path], dict[str, Path]]:
    """Extract raster media and map fallback raster SHA to paired SVG.

    Word stores an SVG picture as an SVG relationship plus a PNG fallback in
    the same DrawingML object.  Mathpix exports 36 such pairs in the PP probe.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    raster_paths: list[Path] = []
    vector_by_fallback_sha: dict[str, Path] = {}
    with zipfile.ZipFile(docx_path) as archive:
        names = set(archive.namelist())
        for name in names:
            suffix = Path(name).suffix.lower()
            if not name.startswith("word/media/") or suffix not in RASTER_EXTENSIONS:
                continue
            output = target_dir / Path(name).name
            output.write_bytes(archive.read(name))
            raster_paths.append(output)

        document_name = "word/document.xml"
        rels_name = "word/_rels/document.xml.rels"
        if document_name not in names or rels_name not in names:
            return raster_paths, vector_by_fallback_sha

        rel_root = etree.fromstring(archive.read(rels_name))
        rel_ns = {"pr": "http://schemas.openxmlformats.org/package/2006/relationships"}
        targets = {
            rel.get("Id"): rel.get("Target")
            for rel in rel_root.xpath("./pr:Relationship", namespaces=rel_ns)
            if rel.get("Id") and rel.get("Target")
        }
        doc_root = etree.fromstring(archive.read(document_name))
        ns = {
            "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
            "asvg": "http://schemas.microsoft.com/office/drawing/2016/SVG/main",
            "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        }
        for blip in doc_root.xpath(".//a:blip[.//asvg:svgBlip]", namespaces=ns):
            fallback_rid = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            svg_nodes = blip.xpath(".//asvg:svgBlip", namespaces=ns)
            if not fallback_rid or not svg_nodes:
                continue
            svg_rid = svg_nodes[0].get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
            fallback_target = targets.get(fallback_rid)
            svg_target = targets.get(svg_rid)
            if not fallback_target or not svg_target:
                continue
            fallback_name = "word/" + fallback_target.lstrip("/")
            svg_name = "word/" + svg_target.lstrip("/")
            if fallback_name not in names or svg_name not in names:
                continue
            svg_output = target_dir / Path(svg_name).name
            svg_output.write_bytes(archive.read(svg_name))
            fallback_sha = hashlib.sha256(archive.read(fallback_name)).hexdigest()
            vector_by_fallback_sha[fallback_sha] = svg_output
    return raster_paths, vector_by_fallback_sha


def extract_docx_raster_assets(docx_path: Path, target_dir: Path) -> list[Path]:
    paths, _ = _extract_docx_media_and_svg_pairs(docx_path, target_dir)
    return paths


def _iter_raster_files(paths: Iterable[Path]) -> Iterable[tuple[Path, str]]:
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        if path.is_file() and path.suffix.lower() in RASTER_EXTENSIONS:
            yield path, "mathpix-external"
        elif path.is_dir():
            for candidate in sorted(path.rglob("*")):
                if candidate.is_file() and candidate.suffix.lower() in RASTER_EXTENSIONS:
                    yield candidate, "mathpix-external"


def build_asset_catalog(reference_docx: Path, external_asset_paths: Iterable[Path], work_dir: Path) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    seen_occurrences: set[tuple[Any, ...]] = set()

    docx_dir = work_dir / "reference_docx_assets"
    docx_rasters, vector_by_sha = _extract_docx_media_and_svg_pairs(reference_docx, docx_dir)

    # External Mathpix bundle assets keep every positional occurrence.  The same
    # binary may legitimately be used more than once on different pages.
    candidates: list[tuple[Path, str]] = list(_iter_raster_files(external_asset_paths))
    candidates.extend((path, "mathpix-docx") for path in docx_rasters)

    external_shas: set[str] = set()
    for path, source in candidates:
        try:
            sha = _sha256(path)
            page, bbox_px = _parse_mathpix_geometry(path) if source == "mathpix-external" else (None, None)
            # A DOCX fallback whose exact binary already exists in the external
            # bundle adds no positional information.  Its SVG pair is attached to
            # the external occurrence through vector_by_sha below.
            if source == "mathpix-docx" and sha in external_shas:
                continue
            width, height, ahash, thumbnail, ink_ratio = _image_metrics(path)
            record = AssetRecord(
                path=path,
                source=source,
                sha256=sha,
                width=width,
                height=height,
                ahash=ahash,
                thumbnail=thumbnail,
                ink_ratio=ink_ratio,
                coordinate_page=page,
                coordinate_bbox_px=bbox_px,
                vector_path=vector_by_sha.get(sha),
            )
            if record.occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(record.occurrence_key)
            records.append(record)
            if source == "mathpix-external":
                external_shas.add(sha)
        except Exception:
            continue
    return records


def _hamming(a: int, b: int) -> int:
    return int((a ^ b).bit_count())


def _area(box: tuple[float, float, float, float] | list[float]) -> float:
    return max(0.0, float(box[2]) - float(box[0])) * max(0.0, float(box[3]) - float(box[1]))


def _intersection(a: tuple[float, float, float, float] | list[float], b: tuple[float, float, float, float] | list[float]) -> float:
    return max(0.0, min(float(a[2]), float(b[2])) - max(float(a[0]), float(b[0]))) * max(
        0.0, min(float(a[3]), float(b[3])) - max(float(a[1]), float(b[1]))
    )


def _record_bbox_pt(record: AssetRecord, page_width_pt: float) -> tuple[float, float, float, float] | None:
    if record.coordinate_bbox_px is None:
        return None
    # Mathpix all-formats image coordinates are measured on a 2048px-wide page
    # canvas.  The same uniform scale applies vertically for portrait/landscape.
    scale = float(page_width_pt) / 2048.0
    x0, y0, x1, y1 = record.coordinate_bbox_px
    return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)


def match_positioned_asset(
    group_bbox: list[float],
    *,
    page_number: int,
    page_width_pt: float,
    catalog: list[AssetRecord],
    used_occurrences: set[tuple[Any, ...]] | None = None,
) -> dict[str, Any] | None:
    """Match a Mathpix image by its exported page coordinates.

    This is the high-leverage fidelity path missing in the previous checkpoint.  It does not ask a
    perceptual matcher to rediscover an image after the PDF has split it into
    labels, equations and small raster fragments.  It uses the page and crop
    geometry already encoded by Mathpix in each downloaded filename.
    """
    used_occurrences = used_occurrences or set()
    group = tuple(map(float, group_bbox))
    group_area = _area(group)
    if group_area <= 1.0:
        return None
    group_diag = math.hypot(group[2] - group[0], group[3] - group[1])
    best: tuple[float, AssetRecord, tuple[float, float, float, float], dict[str, float]] | None = None
    for record in catalog:
        if record.coordinate_page != int(page_number) or record.coordinate_bbox_px is None:
            continue
        if record.occurrence_key in used_occurrences:
            continue
        asset_box = _record_bbox_pt(record, page_width_pt)
        if asset_box is None:
            continue
        inter = _intersection(group, asset_box)
        if inter <= 0.0:
            continue
        asset_area = _area(asset_box)
        union = group_area + asset_area - inter
        iou = inter / max(1.0, union)
        group_coverage = inter / max(1.0, group_area)
        asset_coverage = inter / max(1.0, asset_area)
        gcx, gcy = (group[0] + group[2]) / 2.0, (group[1] + group[3]) / 2.0
        acx, acy = (asset_box[0] + asset_box[2]) / 2.0, (asset_box[1] + asset_box[3]) / 2.0
        center_distance = math.hypot(gcx - acx, gcy - acy) / max(1.0, group_diag)
        group_aspect = (group[2] - group[0]) / max(1.0, group[3] - group[1])
        asset_aspect = (asset_box[2] - asset_box[0]) / max(1.0, asset_box[3] - asset_box[1])
        aspect_delta = abs(group_aspect - asset_aspect) / max(0.05, group_aspect)

        # Exact/near-exact boxes score highest.  A complete Mathpix source image
        # may be larger than the PDF's detected image-only sub-group because the
        # source image also contains labels.  The asset-coverage branch accepts
        # that case while still requiring close centres.
        quality = max(
            iou,
            min(group_coverage, asset_coverage),
            asset_coverage * 0.94 if group_coverage >= 0.25 else 0.0,
            group_coverage * 0.92 if asset_coverage >= 0.75 else 0.0,
        )
        score = quality - center_distance * 0.12 - min(0.40, aspect_delta) * 0.05
        metrics = {
            "iou": iou,
            "groupCoverage": group_coverage,
            "assetCoverage": asset_coverage,
            "centerDistance": center_distance,
            "aspectDelta": aspect_delta,
        }
        accepted = (
            iou >= 0.45
            or (group_coverage >= 0.55 and asset_coverage >= 0.55)
            or (group_coverage >= 0.84 and center_distance <= 0.24)
            or (asset_coverage >= 0.84 and group_coverage >= 0.24 and center_distance <= 0.36)
        ) and center_distance <= 0.40
        if not accepted:
            continue
        if best is None or score > best[0]:
            best = (score, record, asset_box, metrics)
    if best is None:
        return None
    score, record, asset_box, metrics = best
    confidence = max(0.80, min(0.999, 0.72 + max(metrics["iou"], metrics["groupCoverage"], metrics["assetCoverage"]) * 0.27 - metrics["centerDistance"] * 0.08))
    return {
        "record": record,
        "match": "mathpix-page-coordinate",
        "confidence": round(confidence, 4),
        "score": round(score, 5),
        "assetBBoxPt": [round(v, 3) for v in asset_box],
        "mathpixPage": int(record.coordinate_page or 0),
        "mathpixBBoxPx": list(record.coordinate_bbox_px or ()),
        **{key: round(value, 5) for key, value in metrics.items()},
    }


def match_image_asset(pdf_image_path: Path, catalog: list[AssetRecord]) -> dict[str, Any] | None:
    if not pdf_image_path.exists() or not catalog:
        return None
    try:
        pdf_sha = _sha256(pdf_image_path)
        width, height, ahash, thumbnail, ink_ratio = _image_metrics(pdf_image_path)
    except Exception:
        return None

    # Prefer a coordinate-bearing external occurrence over the unpositioned DOCX
    # duplicate when the binary is exactly the same.
    exact = [record for record in catalog if record.sha256 == pdf_sha]
    if exact:
        record = sorted(exact, key=lambda r: (r.coordinate_page is not None, r.source == "mathpix-external"), reverse=True)[0]
        return {
            "record": record,
            "match": "exact-sha256",
            "confidence": 1.0,
            "hamming": 0,
        }

    aspect = width / max(1, height)
    best: tuple[float, AssetRecord, int, float, float, float] | None = None
    # Perceptual matching works on unique binaries.  Repeated positional copies
    # of the same external image need not be compared repeatedly here.
    unique_records: dict[str, AssetRecord] = {}
    for record in catalog:
        current = unique_records.get(record.sha256)
        if current is None or (record.coordinate_page is not None and current.coordinate_page is None):
            unique_records[record.sha256] = record
    for record in unique_records.values():
        aspect_delta = abs(record.aspect - aspect) / max(0.01, aspect)
        if aspect_delta > 0.065:
            continue
        distance = _hamming(ahash, record.ahash)
        if distance > 38:
            continue
        pixel_mae = sum(abs(a - b) for a, b in zip(thumbnail, record.thumbnail)) / max(1, len(thumbnail))
        ink_delta = abs(ink_ratio - record.ink_ratio)
        if pixel_mae > 34.0 or ink_delta > 0.22:
            continue
        dimension_ratio = min(width / max(1, record.width), record.width / max(1, width), height / max(1, record.height), record.height / max(1, height))
        score = distance + pixel_mae * 1.35 + aspect_delta * 90.0 + ink_delta * 60.0 + (1.0 - dimension_ratio) * 6.0
        if best is None or score < best[0]:
            best = (score, record, distance, aspect_delta, pixel_mae, ink_delta)
    if best is None:
        return None
    score, record, distance, aspect_delta, pixel_mae, ink_delta = best
    confidence = max(0.0, min(0.99, 1.0 - distance / 72.0 - pixel_mae / 115.0 - aspect_delta * 0.35 - ink_delta * 0.25))
    if confidence < 0.80:
        return None
    return {
        "record": record,
        "match": "perceptual",
        "confidence": round(confidence, 4),
        "hamming": distance,
        "aspectDelta": round(aspect_delta, 5),
        "pixelMae": round(pixel_mae, 4),
        "inkDelta": round(ink_delta, 5),
        "score": round(score, 4),
    }


def materialize_asset(match: dict[str, Any], target_dir: Path, group_id: str) -> dict[str, Path | None]:
    record: AssetRecord = match["record"]
    prefix = "docx" if record.source == "mathpix-docx" else "bundle"
    safe_group = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in group_id)[:70]
    target = _safe_copy(record.path, target_dir, f"{safe_group}_{prefix}")
    # Some Mathpix JPEGs are valid for browsers/Pillow but omit the APP marker
    # expected by python-docx. Preserve the original when Word accepts it; only
    # then fall back to a lossless PNG normalization.
    try:
        from docx.image.image import Image as DocxImage
        DocxImage.from_file(str(target))
        raster_target = target
    except Exception:
        normalized = target.with_suffix(".png")
        with Image.open(target) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode not in {"RGB", "RGBA", "L"}:
                image = image.convert("RGBA")
            image.save(normalized, format="PNG")
        match["formatNormalized"] = True
        raster_target = normalized

    vector_target: Path | None = None
    if record.vector_path and record.vector_path.exists():
        vector_target = _safe_copy(record.vector_path, target_dir, f"{safe_group}_vector")
        match["vectorAvailable"] = True
        match["vectorSource"] = str(record.vector_path)
    return {"raster": raster_target, "svg": vector_target}
