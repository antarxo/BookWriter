from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable


def parse_page_range(value: str, max_pages: int | None = None) -> list[int]:
    """Parse a 1-based range such as ``17-20,25`` into sorted page numbers."""
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise ValueError("Δεν δόθηκαν έγκυρες σελίδες.")
    if min(pages) < 1:
        raise ValueError("Οι σελίδες είναι 1-based και πρέπει να είναι >= 1.")
    if max_pages is not None and max(pages) > max_pages:
        raise ValueError(f"Η σελίδα {max(pages)} υπερβαίνει το PDF ({max_pages} σελίδες).")
    return sorted(pages)


def normalize_text(text: str) -> str:
    """Normalize text for tolerant PDF ↔ DOCX matching."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00ad", "")  # soft hyphen
    text = text.replace("‐", "-").replace("–", "-").replace("—", "-")
    text = re.sub(r"-\s*\n\s*(?=\w)", "", text)
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text


def compact_text(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Path):
            return str(obj)
        raise TypeError(f"Cannot serialize {type(obj)!r}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=default), encoding="utf-8")


def safe_filename(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    return text.strip("_") or "item"


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0
