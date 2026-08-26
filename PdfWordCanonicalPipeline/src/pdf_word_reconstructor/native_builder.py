from __future__ import annotations

from pathlib import Path
from typing import Any

# The previous HF55 builder is preserved verbatim.  This module is now the
# mandatory maps-first gate used by every existing call site.
from .native_builder_legacy import *  # noqa: F401,F403
from .native_builder_legacy import build_native_page_document as _legacy_build_native_page_document
from .build_contract import build_build_contract
from .common import write_json


def _analysis_dir_for_output(output_path: Path) -> Path | None:
    """Resolve the established run analysis directory without inventing a path.

    CLI outputs are either directly under RUN/ or under RUN/work/calibration/.
    We only select an ancestor whose existing `analysis` directory proves that it
    is the run root.  If no such directory exists, no diagnostic side effect is
    attempted.
    """
    output_path = Path(output_path)
    candidates = [output_path.parent, *output_path.parents]
    seen: set[str] = set()
    for root in candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        analysis_dir = root / "analysis"
        if analysis_dir.is_dir():
            return analysis_dir
    return None


def _prepare_build_contract(
    page_layout_spine: dict[str, Any] | None,
    output_path: Path,
) -> dict[str, Any]:
    if not page_layout_spine:
        raise RuntimeError(
            "Maps-first build blocked: page_layout_spine is required before native Word build."
        )

    contract = build_build_contract(page_layout_spine)
    analysis_dir = _analysis_dir_for_output(Path(output_path))
    if analysis_dir is not None:
        write_json(analysis_dir / "build_contract.json", contract)

    summary = contract.get("summary") or {}
    unresolved = int(summary.get("unresolvedCount") or 0)
    if unresolved:
        reasons = summary.get("unresolvedReasonCounts") or {}
        details = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
        raise RuntimeError(
            "Maps-first build contract unresolved: "
            f"{unresolved}/{int(summary.get('itemCount') or 0)} item(s). "
            f"{details or 'See build_contract.json.'}"
        )

    return contract


def build_native_page_document(
    pdf_analysis: dict[str, Any],
    page_structure: dict[str, Any],
    alignment: dict[str, Any],
    docx_analysis: dict[str, Any],
    style_profile: dict[str, Any],
    output_path: Path,
    body_size_override: float | None = None,
    font_scale: float = 1.0,
    gap_scale: float = 0.72,
    body_line_spacing_multiple: float | None = None,
    docx_donor_map: dict[str, Any] | None = None,
    page_layout_spine: dict[str, Any] | None = None,
    flow_mode: str = "free",
) -> dict[str, Any]:
    """Mandatory maps-first gate followed by the preserved HF55 renderer.

    This is a transitional execution boundary: the legacy implementation still
    performs the physical Word rendering, but it cannot run unless the complete
    pre-build contract is ready.  No PDF/DOCX text fallback is permitted to hide
    an unresolved mapping at this boundary.
    """
    contract = _prepare_build_contract(page_layout_spine, Path(output_path))

    report = _legacy_build_native_page_document(
        pdf_analysis,
        page_structure,
        alignment,
        docx_analysis,
        style_profile,
        output_path,
        body_size_override=body_size_override,
        font_scale=font_scale,
        gap_scale=gap_scale,
        body_line_spacing_multiple=body_line_spacing_multiple,
        docx_donor_map=docx_donor_map,
        page_layout_spine=page_layout_spine,
        flow_mode=flow_mode,
    )
    if isinstance(report, dict):
        report["build_contract"] = {
            "version": contract.get("version"),
            "status": "ready",
            "summary": contract.get("summary") or {},
            "policy": contract.get("policy") or {},
        }
        report["execution_boundary"] = {
            "policy": "maps-first-contract-gate-before-legacy-renderer",
            "contractCheckedBeforeRender": True,
            "silentFallbackAllowed": False,
            "legacyRendererStillActive": True,
        }
    return report
