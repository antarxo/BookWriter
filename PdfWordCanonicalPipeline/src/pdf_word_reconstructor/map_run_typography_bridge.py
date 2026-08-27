from __future__ import annotations

import importlib
from contextlib import contextmanager
from difflib import SequenceMatcher
from typing import Any, Iterator

from .contract_typography_bridge import contract_typography_bridge as _base_contract_typography_bridge


VERSION = "map-run-typography-bridge-0.2"


def _style_payload(run: dict[str, Any]) -> dict[str, Any]:
    style = run.get("style") if isinstance(run.get("style"), dict) else {}
    return {
        "bold": bool(style.get("bold")),
        "italic": bool(style.get("italic")),
        "underline": bool(style.get("underline")),
        "superscript": bool(style.get("superscript")),
        "font": run.get("fontFamily"),
        "size_pt": run.get("fontSizePt"),
        "pdf_color": run.get("color"),
        "__contract": True,
        "__pageMapRun": True,
    }


def _style_key(style: dict[str, Any] | None) -> tuple[Any, ...]:
    row = style or {}
    return (
        bool(row.get("bold")),
        bool(row.get("italic")),
        bool(row.get("underline")),
        bool(row.get("superscript")),
        str(row.get("font") or ""),
        row.get("size_pt"),
        str(row.get("pdf_color") or ""),
    )


def _source_chars(map_runs: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any] | None]]:
    chars: list[str] = []
    styles: list[dict[str, Any] | None] = []
    previous_style: dict[str, Any] | None = None
    for raw in map_runs:
        if raw.get("lineBreak"):
            chars.append("\n")
            styles.append(previous_style)
            continue
        text = str(raw.get("text") or "")
        if not text:
            continue
        style = _style_payload(raw)
        previous_style = style
        chars.extend(text)
        styles.extend([style] * len(text))
    return "".join(chars), styles


def _nearest_style(styles: list[dict[str, Any] | None], index: int) -> dict[str, Any] | None:
    if not styles:
        return None
    index = max(0, min(len(styles) - 1, index))
    if styles[index] is not None:
        return styles[index]
    for delta in range(1, len(styles)):
        left = index - delta
        right = index + delta
        if left >= 0 and styles[left] is not None:
            return styles[left]
        if right < len(styles) and styles[right] is not None:
            return styles[right]
    return None


def _project_runs(authoritative_text: str, map_runs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    source_text, source_styles = _source_chars(map_runs)
    target = str(authoritative_text or "")
    if not target or not source_text or not source_styles:
        return [], 0.0

    matcher = SequenceMatcher(None, source_text, target, autojunk=False)
    target_styles: list[dict[str, Any] | None] = [None] * len(target)
    for match in matcher.get_matching_blocks():
        if not match.size:
            continue
        for offset in range(match.size):
            source_index = match.a + offset
            target_index = match.b + offset
            if source_index < len(source_styles) and target_index < len(target_styles):
                target_styles[target_index] = source_styles[source_index]

    # Markdown/build-contract text remains authoritative. Characters without an
    # exact PDF counterpart inherit the nearest mapped PDF style; text is never
    # replaced by the PDF witness.
    last_style: dict[str, Any] | None = None
    for index, value in enumerate(target_styles):
        if value is not None:
            last_style = value
        elif last_style is not None:
            target_styles[index] = last_style
    next_style: dict[str, Any] | None = None
    for index in range(len(target_styles) - 1, -1, -1):
        value = target_styles[index]
        if value is not None:
            next_style = value
        elif next_style is not None:
            target_styles[index] = next_style
    fallback = _nearest_style(source_styles, 0)
    if fallback is not None:
        target_styles = [value if value is not None else fallback for value in target_styles]

    output: list[dict[str, Any]] = []
    start = 0
    while start < len(target):
        key = _style_key(target_styles[start])
        end = start + 1
        while end < len(target) and _style_key(target_styles[end]) == key:
            end += 1
        style = dict(target_styles[start] or fallback or {})
        style["text"] = target[start:end]
        output.append(style)
        start = end
    return output, round(float(matcher.ratio()), 5)


@contextmanager
def map_run_typography_bridge(
    legacy_module: Any,
    contract: dict[str, Any],
    page_structure: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """Layer page-map PDF runs over the existing maps-first contract bridge."""
    with _base_contract_typography_bridge(legacy_module, contract, page_structure) as audit:
        original_item_text = legacy_module._item_text_and_runs
        stats = {
            "version": VERSION,
            "mappedRunItemCount": 0,
            "fallbackItemCount": 0,
            "projectionRatioMin": None,
            "projectionRatioSum": 0.0,
            "projectedRunCount": 0,
            "policy": "authoritative-contract-text-with-page-map-run-typography",
        }

        def item_text_and_runs(item: dict[str, Any], matches: dict[str, Any], docx_paras: dict[str, Any]):
            text, source_runs, source, paragraph_ids = original_item_text(item, matches, docx_paras)
            style_map = item.get("textStyleMap") if isinstance(item.get("textStyleMap"), dict) else {}
            map_runs = style_map.get("runs") if isinstance(style_map.get("runs"), list) else []
            if item.get("type") == "text" and map_runs and text:
                projected, ratio = _project_runs(text, map_runs)
                if projected:
                    stats["mappedRunItemCount"] += 1
                    stats["projectedRunCount"] += len(projected)
                    stats["projectionRatioSum"] += ratio
                    current_min = stats.get("projectionRatioMin")
                    stats["projectionRatioMin"] = ratio if current_min is None else min(float(current_min), ratio)
                    return text, projected, f"{source}+page-map-runs", paragraph_ids
            stats["fallbackItemCount"] += 1
            return text, source_runs, source, paragraph_ids

        legacy_module._item_text_and_runs = item_text_and_runs
        try:
            audit["runTypography"] = stats
            yield audit
        finally:
            count = int(stats.get("mappedRunItemCount") or 0)
            stats["projectionRatioAverage"] = round(float(stats.get("projectionRatioSum") or 0.0) / count, 5) if count else None
            stats.pop("projectionRatioSum", None)
            legacy_module._item_text_and_runs = original_item_text


def install_as_contract_bridge() -> None:
    """Make the run-aware bridge canonical before native_builder imports it."""
    module = importlib.import_module(".contract_typography_bridge", __package__)
    module.contract_typography_bridge = map_run_typography_bridge


__all__ = ["map_run_typography_bridge", "install_as_contract_bridge"]
