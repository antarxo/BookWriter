from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .asset_resolver import AssetRecord, RASTER_EXTENSIONS, _image_metrics, _parse_mathpix_geometry, _sha256


def build_external_asset_catalog(paths: Iterable[Path]) -> list[AssetRecord]:
    """Catalog external Mathpix raster assets without requiring a DOCX donor."""
    records: list[AssetRecord] = []
    seen: set[tuple] = set()
    for raw in paths:
        path = Path(raw)
        candidates: list[Path] = []
        if path.is_file() and path.suffix.lower() in RASTER_EXTENSIONS:
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(
                candidate for candidate in sorted(path.rglob("*"))
                if candidate.is_file() and candidate.suffix.lower() in RASTER_EXTENSIONS
            )
        for candidate in candidates:
            try:
                sha = _sha256(candidate)
                page, bbox_px = _parse_mathpix_geometry(candidate)
                width, height, ahash, thumbnail, ink_ratio = _image_metrics(candidate)
                record = AssetRecord(
                    path=candidate,
                    source="mathpix-external",
                    sha256=sha,
                    width=width,
                    height=height,
                    ahash=ahash,
                    thumbnail=thumbnail,
                    ink_ratio=ink_ratio,
                    coordinate_page=page,
                    coordinate_bbox_px=bbox_px,
                    vector_path=None,
                )
                if record.occurrence_key in seen:
                    continue
                seen.add(record.occurrence_key)
                records.append(record)
            except Exception:
                continue
    return records


__all__ = ["build_external_asset_catalog"]
