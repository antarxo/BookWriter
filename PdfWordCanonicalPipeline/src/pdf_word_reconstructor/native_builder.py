from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

# The previous HF55 builder is preserved verbatim. This module is the mandatory
# maps-first execution boundary used by every existing call site.
from .native_builder_legacy import *  # noqa: F401,F403
from . import native_builder_legacy as _legacy_module
from .native_builder_legacy import build_native_page_document as _legacy_build_native_page_document
from .build_contract import build_build_contract
from .common import write_json
from .contract_typography_bridge import contract_typography_bridge


_TEXT_OUTPUT_KINDS = {"paragraph", "heading", "caption", "callout", "list"}


def _analysis_dir_for_output(output_path: Path) -> Path | None:
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


def _contract_text(item: dict[str, Any]) -> str:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    authoritative = item.get("authoritativeContent") if isinstance(item.get("authoritativeContent"), dict) else {}
    for value in (content.get("text"), authoritative.get("text")):
        text = str(value or "")
        if text:
            return text
    return ""


def _contract_by_slot(contract: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for item in contract.get("items", []) or []:
        placement = item.get("placement") or {}
        page = placement.get("page")
        slot_id = placement.get("slotId")
        if page is None or not slot_id:
            continue
        try:
            key = (int(page), str(slot_id))
        except (TypeError, ValueError):
            continue
        if key in result:
            raise RuntimeError(f"Maps-first build blocked: duplicate contract binding for slot {key}.")
        result[key] = item
    return result


def _slot_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for value in (item.get("id"), item.get("visual_group_id")):
        if value:
            keys.append(str(value))
    item_id = str(item.get("id") or "")
    if item_id.startswith("flow-"):
        keys.append(item_id[5:])
    return list(dict.fromkeys(keys))


def _find_contract_item(
    by_slot: dict[tuple[int, str], dict[str, Any]],
    page_no: int,
    item: dict[str, Any],
) -> dict[str, Any] | None:
    for key in _slot_keys(item):
        found = by_slot.get((page_no, key))
        if found is not None:
            return found
    return None


def _materialize_contract_text(
    page_structure: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the renderer input from the pre-built contract.

    Text-bearing flow/callout objects receive Markdown-authoritative text here.
    The legacy renderer is therefore no longer allowed to choose prose from PDF
    text or donor DOCX text. Missing bindings are fatal instead of falling back.
    """
    materialized = deepcopy(page_structure)
    by_slot = _contract_by_slot(contract)
    bound = 0
    missing: list[str] = []
    lineage: list[dict[str, Any]] = []

    for page in materialized.get("pages", []) or []:
        page_no = int(page.get("page") or 0)

        for flow_item in page.get("flow", []) or []:
            if str(flow_item.get("type") or "") != "text":
                continue
            contract_item = _find_contract_item(by_slot, page_no, flow_item)
            slot_id = str(flow_item.get("id") or "")
            if contract_item is None:
                missing.append(f"page={page_no}:flow={slot_id}")
                continue
            output_kind = str(contract_item.get("outputKind") or "")
            if output_kind not in _TEXT_OUTPUT_KINDS:
                continue
            text = _contract_text(contract_item)
            if not text.strip():
                missing.append(f"page={page_no}:flow={slot_id}:empty-markdown")
                continue
            flow_item["text"] = text
            flow_item["content_source"] = "markdown-via-build-contract"
            flow_item["markdown_id"] = contract_item.get("markdownId")
            flow_item["__buildContractId"] = contract_item.get("id")
            flow_item["__wordParagraph"] = contract_item.get("wordParagraph") or {}
            flow_item["__pdfTypography"] = contract_item.get("pdfTypography") or {}
            bound += 1
            lineage.append({
                "page": page_no,
                "slotId": slot_id,
                "markdownId": contract_item.get("markdownId"),
                "outputKind": output_kind,
                "contentSource": "markdown-via-build-contract",
                "typographySource": "pdf-via-build-contract",
            })

        for callout in page.get("callouts", []) or []:
            contract_item = _find_contract_item(by_slot, page_no, callout)
            slot_id = str(callout.get("id") or "")
            if contract_item is None:
                missing.append(f"page={page_no}:callout={slot_id}")
                continue
            text = _contract_text(contract_item)
            if not text.strip():
                missing.append(f"page={page_no}:callout={slot_id}:empty-markdown")
                continue
            callout["text"] = text
            callout["content_source"] = "markdown-via-build-contract"
            callout["markdown_id"] = contract_item.get("markdownId")
            callout["__buildContractId"] = contract_item.get("id")
            callout["__wordParagraph"] = contract_item.get("wordParagraph") or {}
            callout["__pdfTypography"] = contract_item.get("pdfTypography") or {}
            bound += 1
            lineage.append({
                "page": page_no,
                "slotId": slot_id,
                "markdownId": contract_item.get("markdownId"),
                "outputKind": "callout",
                "contentSource": "markdown-via-build-contract",
                "typographySource": "pdf-via-build-contract",
            })

    if missing:
        preview = "; ".join(missing[:12])
        raise RuntimeError(
            "Maps-first build blocked: renderer text slot(s) lack authoritative contract binding: "
            f"{preview}"
        )

    return materialized, {
        "policy": "contract-materialized-markdown-text-and-pdf-typography",
        "boundTextSlotCount": bound,
        "missingTextSlotCount": len(missing),
        "items": lineage,
    }


def _sanitized_alignment(alignment: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve diagnostics only; remove donor/PDF prose matching from rendering."""
    source = alignment or {}
    return {
        "summary": deepcopy(source.get("summary") or {}),
        "matches": [],
        "renderPolicy": "alignment-matches-disabled-for-text-authority",
    }


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
    """Mandatory maps-first boundary followed by the preserved Word renderer.

    The physical renderer is still the preserved HF55 implementation. Its prose,
    ordinary typography and paragraph geometry are materialized from the build
    contract before save; legacy alignment matches are removed.
    """
    contract = _prepare_build_contract(page_layout_spine, Path(output_path))
    materialized_structure, text_lineage = _materialize_contract_text(page_structure, contract)
    render_alignment = _sanitized_alignment(alignment)

    with contract_typography_bridge(_legacy_module, contract, materialized_structure) as paragraph_audit:
        report = _legacy_build_native_page_document(
            pdf_analysis,
            materialized_structure,
            render_alignment,
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
        report["content_lineage"] = text_lineage
        report["typography_lineage"] = {
            "policy": "ordinary-flow-typography-from-build-contract",
            "fontFamily": "pdfTypography.fontFamily.dominant",
            "fontSize": "pdfTypography.fontSizePt.dominant",
            "emphasis": "pdfTypography.emphasis",
            "color": "pdfTypography.color.dominant",
            "lineHeight": "wordParagraph.geometry.lineHeightPt",
            "legacyTimesNewRomanForOrdinaryFlow": False,
        }
        report["paragraph_format_lineage"] = {
            "policy": "observed-word-paragraph-format-from-build-contract-before-save",
            "alignment": "wordParagraph.geometry.alignment",
            "leftIndent": "wordParagraph.geometry.leftIndentPt",
            "rightIndent": "wordParagraph.geometry.rightIndentPt",
            "firstLineIndent": "wordParagraph.geometry.firstLineIndentPt",
            "hangingIndent": "wordParagraph.geometry.hangingIndentPt",
            "lineHeight": "wordParagraph.geometry.lineHeightPt",
            "spaceBefore": "wordParagraph.spacing.spaceBeforePt",
            "spaceAfter": "wordParagraph.spacing.spaceAfterPt",
            "audit": paragraph_audit,
        }
        report["execution_boundary"] = {
            "policy": "maps-first-contract-materialization-before-legacy-renderer",
            "contractCheckedBeforeRender": True,
            "markdownTextMaterializedBeforeRender": True,
            "ordinaryFlowTypographyMaterializedBeforeRender": True,
            "paragraphFormatReappliedFromContractBeforeSave": True,
            "alignmentMatchesVisibleToRenderer": False,
            "silentFallbackAllowed": False,
            "legacyRendererStillActive": True,
        }
    return report
