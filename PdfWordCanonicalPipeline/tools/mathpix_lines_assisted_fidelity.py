from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any


LINES_ONLY_TOP_LEVEL_KEYS = (
    "mathpixLineLayoutMap",
    "mathpixLinesSummary",
)
LINES_ONLY_PAGE_KEYS = (
    "mathpixLinePageMap",
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the existing fidelity -> reconstructed DOCX -> canonical Word pipeline "
            "with selectable Mathpix Lines priority inside page_structure."
        )
    )
    p.add_argument("--pdf", required=True, type=Path)
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--lines", type=Path, default=None, help="Optional Mathpix result.lines.json")
    p.add_argument(
        "--mode",
        choices=("off", "witness", "lines-first"),
        default=None,
        help=(
            "off = ordinary pipeline; witness = current Lines-assisted reconciliation; "
            "lines-first = Lines hierarchy leads existing structural page_structure fields. "
            "If omitted, mode is inferred as off without --lines and witness with --lines."
        ),
    )
    p.add_argument("--pages", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    p.add_argument("--strict-page-count", action="store_true")
    p.add_argument("--no-render", action="store_true")
    return p


def _strip_lines_only_interface_fields(result: dict) -> dict:
    """Keep the downstream page_structure interface identical in all three modes."""
    for key in LINES_ONLY_TOP_LEVEL_KEYS:
        result.pop(key, None)
    for page in result.get("pages", []) or []:
        for key in LINES_ONLY_PAGE_KEYS:
            page.pop(key, None)
    return result


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = [float(part) for part in value]
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _relative_box(box: list[float] | None, page: dict[str, Any] | None) -> list[float] | None:
    if box is None or not page:
        return None
    try:
        width = float(page.get("width_pt") or 0.0)
        height = float(page.get("height_pt") or 0.0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return [
        round(box[0] / width, 6),
        round(box[1] / height, 6),
        round(box[2] / width, 6),
        round(box[3] / height, 6),
    ]


def _record_box(record: dict[str, Any]) -> list[float] | None:
    bbox = record.get("bbox_pt") if isinstance(record.get("bbox_pt"), dict) else {}
    if not bbox:
        return None
    return _box([bbox.get("x0"), bbox.get("y0"), bbox.get("x1"), bbox.get("y1")])


def _area(box: list[float]) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a: list[float], b: list[float]) -> float:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _contains(outer: list[float], inner: list[float], tolerance: float = 4.0) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


def _best_record_match(item_box: list[float], records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match only to concrete Lines objects; container hierarchy is used afterwards."""
    ignored_types = {"column", "page_info"}
    best: tuple[float, dict[str, Any]] | None = None
    item_area = max(1.0, _area(item_box))
    for record in records:
        if str(record.get("type") or "") in ignored_types:
            continue
        record_box = _record_box(record)
        if record_box is None:
            continue
        overlap_area = _intersection(item_box, record_box)
        record_area = max(1.0, _area(record_box))
        overlap = overlap_area / min(item_area, record_area)
        contains = _contains(record_box, item_box) or _contains(item_box, record_box)
        if overlap < 0.30 and not contains:
            continue
        score = overlap + (0.20 if contains else 0.0)
        if best is None or score > best[0]:
            best = (score, record)
    return best[1] if best else None


def _ancestor_chain(record: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    parent_id = str(record.get("parent_id") or "")
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break
        chain.append(parent)
        parent_id = str(parent.get("parent_id") or "")
    return chain


def _semantic_type(record_type: str, current: str | None) -> str | None:
    mapping = {
        "section_header": "heading",
        "figure_label": "caption",
        "diagram": "figure",
        "text": "paragraph",
        "math": "equation",
    }
    return mapping.get(record_type, current)


def _column_index_from_explicit_ancestor(
    record: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    pdf_columns: list[dict[str, Any]],
) -> int | None:
    """Lines may assign a column only when PDF already confirmed a two-column page."""
    if len(pdf_columns) != 2:
        return None
    column_ancestor = next(
        (ancestor for ancestor in _ancestor_chain(record, by_id) if str(ancestor.get("type") or "") == "column"),
        None,
    )
    if column_ancestor is None:
        return None
    column_box = _record_box(column_ancestor)
    if column_box is None:
        return None
    center = (column_box[0] + column_box[2]) / 2.0
    candidates: list[tuple[float, int]] = []
    for index, pdf_column in enumerate(pdf_columns):
        try:
            x0 = float(pdf_column.get("x0"))
            x1 = float(pdf_column.get("x1"))
        except (TypeError, ValueError):
            continue
        candidates.append((abs(center - ((x0 + x1) / 2.0)), index))
    return min(candidates)[1] if candidates else None


def _apply_lines_first_hierarchy(result: dict[str, Any]) -> dict[str, Any]:
    """Experimental LINES_FIRST policy, isolated from OFF/WITNESS.

    Lines hierarchy is authoritative for semantic role and sibling order. It does not
    manufacture Word columns and it does not globally sort unrelated containers.
    PDF-confirmed page/column geometry remains intact.
    """
    matched_count = 0
    semantic_count = 0
    sibling_reorders = 0
    explicit_column_assignments = 0
    hierarchy_matches = 0

    for page in result.get("pages", []) or []:
        line_page = page.get("mathpixLinePageMap") if isinstance(page.get("mathpixLinePageMap"), dict) else None
        if not line_page:
            continue
        records = list(line_page.get("objects", []) or [])
        by_id = {
            str(record.get("id")): record
            for record in records
            if record.get("id")
        }
        flow = list(page.get("flow", []) or [])
        pdf_columns = list(page.get("columns", []) or []) if str(page.get("layout_mode") or "") == "two_columns" else []

        groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

        for index, item in enumerate(flow):
            item_box = _box(item.get("bbox"))
            if item_box is None:
                continue
            record = _best_record_match(item_box, records)
            if record is None:
                continue
            matched_count += 1

            previous_semantic = item.get("semantic_type")
            semantic = _semantic_type(str(record.get("type") or ""), previous_semantic)
            if semantic is not None and semantic != previous_semantic:
                item["semantic_type"] = semantic
                semantic_count += 1

            parent_id = str(record.get("parent_id") or "")
            if parent_id:
                groups[parent_id].append((index, record))
                hierarchy_matches += 1

            column_index = _column_index_from_explicit_ancestor(record, by_id, pdf_columns)
            if column_index is not None:
                item["column_index"] = column_index
                item["spanning"] = False
                explicit_column_assignments += 1

        # Reorder only siblings that Lines explicitly says belong to the same parent.
        # Their occupied flow slots remain fixed, so unrelated containers cannot jump
        # ahead of or behind one another merely because of geometric rank.
        for parent_id, members in groups.items():
            if len(members) < 2:
                continue
            parent = by_id.get(parent_id) or {}
            child_ids = [str(value) for value in (parent.get("children_ids") or []) if value]
            child_rank = {child_id: rank for rank, child_id in enumerate(child_ids)}
            if not child_rank:
                continue
            slots = sorted(index for index, _record in members)
            ordered_members = sorted(
                members,
                key=lambda pair: (
                    child_rank.get(str(pair[1].get("id") or ""), 1000000),
                    int(pair[1].get("line") or 1000000),
                    pair[0],
                ),
            )
            current_items = [flow[index] for index in slots]
            reordered_items = [flow[index] for index, _record in ordered_members]
            if [id(item) for item in current_items] == [id(item) for item in reordered_items]:
                continue
            for slot, item in zip(slots, reordered_items):
                flow[slot] = item
            sibling_reorders += 1

        page["flow"] = flow

    return {
        "matchedFlowItemCount": matched_count,
        "semanticTypeChangeCount": semantic_count,
        "hierarchyMatchedItemCount": hierarchy_matches,
        "siblingGroupReorderCount": sibling_reorders,
        "explicitColumnAssignmentCount": explicit_column_assignments,
        "policy": (
            "LINES_FIRST hierarchy policy: parent/children structure leads semantic role and sibling order; "
            "Lines column ancestry may refine ownership only on PDF-confirmed two-column pages; "
            "no new Word columns and no global cross-container sorting are allowed."
        ),
    }


def _page_structure_slots(page_structure: dict[str, Any]) -> tuple[dict[tuple[int, str], dict[str, Any]], dict[str, int]]:
    slots: dict[tuple[int, str], dict[str, Any]] = {}
    order_by_slot: dict[str, int] = {}
    global_order = 0
    for page in page_structure.get("pages", []) or []:
        page_no = int(page.get("page") or 0)
        for order, item in enumerate(page.get("flow", []) or []):
            slot_id = str(item.get("id") or "")
            if not slot_id:
                continue
            slot = {
                "page": page_no,
                "slotId": slot_id,
                "source": "page_structure.flow",
                "type": item.get("type"),
                "semanticType": item.get("semantic_type"),
                "bbox": _box(item.get("bbox")),
                "columnIndex": item.get("column_index"),
                "spanning": bool(item.get("spanning")),
                "flowOrder": order,
                "wordFlowOrder": global_order,
                "page": page,
            }
            slots[(page_no, slot_id)] = slot
            order_by_slot[f"{page_no}:{slot_id}"] = global_order
            global_order += 1
        for group in page.get("visual_groups", []) or []:
            slot_id = str(group.get("id") or "")
            if slot_id:
                slots[(page_no, slot_id)] = {
                    "page": page_no,
                    "slotId": slot_id,
                    "source": "page_structure.visual_group",
                    "type": "visual",
                    "semanticType": group.get("kind"),
                    "bbox": _box(group.get("bbox")),
                    "columnIndex": None,
                    "spanning": group.get("placement") == "floating",
                    "placement": group.get("placement"),
                    "flowOrder": None,
                    "wordFlowOrder": None,
                    "page": page,
                }
        for callout in page.get("callouts", []) or []:
            slot_id = str(callout.get("id") or "")
            if slot_id:
                slots[(page_no, slot_id)] = {
                    "page": page_no,
                    "slotId": slot_id,
                    "source": "page_structure.callout",
                    "type": "callout",
                    "semanticType": "callout",
                    "bbox": _box(callout.get("bbox")),
                    "columnIndex": None,
                    "spanning": True,
                    "flowOrder": None,
                    "wordFlowOrder": None,
                    "page": page,
                }
    return slots, order_by_slot


def _column_box_for_slot(page: dict[str, Any], column_index: Any, spanning: bool) -> dict[str, float] | None:
    columns = [item for item in page.get("columns", []) or [] if isinstance(item, dict)]
    if spanning and columns:
        try:
            return {
                "x0": min(float(item.get("x0")) for item in columns),
                "x1": max(float(item.get("x1")) for item in columns),
                "y0": min(float(item.get("y0")) for item in columns),
                "y1": max(float(item.get("y1")) for item in columns),
            }
        except (TypeError, ValueError):
            pass
    try:
        index = int(column_index)
    except (TypeError, ValueError):
        index = -1
    if 0 <= index < len(columns):
        return dict(columns[index])
    main = page.get("main_column")
    return dict(main) if isinstance(main, dict) else None


def _placement_for_slot(slot: dict[str, Any]) -> str:
    source = str(slot.get("source") or "")
    page = slot.get("page") if isinstance(slot.get("page"), dict) else {}
    if source == "page_structure.callout":
        return "positioned-text-frame"
    if source == "page_structure.visual_group":
        return "floating-visual" if slot.get("placement") == "floating" else "inline-visual"
    if slot.get("spanning"):
        return "spanning-text-frame"
    if str(page.get("layout_mode") or "") == "two_columns" and slot.get("columnIndex") is not None:
        return "word-column-flow"
    return "normal-flow"


def _apply_lines_first_layout_consume_only(
    spine: dict[str, Any],
    page_structure: dict[str, Any],
) -> dict[str, Any]:
    """Lock structural layout fields to the already-built page_structure.

    The ordinary page-layout module may still provide content, typography and donor
    payloads, but it is not allowed to supersede page/slot/order/column/placement
    when an exact page_structure slot exists. Non-exact rows are retained only as
    legacy content fallbacks and are counted explicitly.
    """
    slots, order_by_slot = _page_structure_slots(page_structure)
    locked = 0
    inherited = 0

    for row in spine.get("rows", []) or []:
        layout = row.get("layout") if isinstance(row.get("layout"), dict) else {}
        try:
            page_no = int(layout.get("page") or 0)
        except (TypeError, ValueError):
            page_no = 0
        slot_id = str(layout.get("slotId") or "")
        slot = slots.get((page_no, slot_id)) if page_no and slot_id else None
        if slot is None:
            inherited += 1
            continue

        page = slot.get("page") if isinstance(slot.get("page"), dict) else {}
        box = slot.get("bbox")
        column_index = slot.get("columnIndex")
        spanning = bool(slot.get("spanning"))
        placement = _placement_for_slot(slot)
        column_role = "span" if spanning else (f"col-{column_index}" if column_index is not None else "main")

        layout.update({
            "status": "layout-slot",
            "matchMode": "lines-first-page-structure-consume-only",
            "score": 100.0,
            "page": page_no,
            "slotId": slot_id,
            "slotSource": slot.get("source"),
            "slotType": slot.get("type"),
            "semanticType": slot.get("semanticType"),
            "bbox": box,
            "columnIndex": column_index,
            "columnRole": column_role,
            "spanning": spanning,
            "flowOrder": slot.get("flowOrder"),
            "wordFlowOrder": slot.get("wordFlowOrder"),
        })
        row["layout"] = layout

        contract = row.get("layoutContract") if isinstance(row.get("layoutContract"), dict) else {}
        contract["status"] = "usable"
        contract["page"] = page_no
        contract["layoutMode"] = page.get("layout_mode")
        contract["slot"] = {
            "id": slot_id,
            "source": slot.get("source"),
            "type": slot.get("type"),
            "semanticType": slot.get("semanticType"),
        }
        contract["column"] = {
            "index": column_index,
            "role": column_role,
            "box": _column_box_for_slot(page, column_index, spanning),
            "spanning": spanning,
        }
        contract["box"] = {
            "absolutePt": box,
            "relativePage": _relative_box(box, page),
            "source": "page_structure-slot",
        }
        contract["placement"] = placement
        builder_use = contract.get("builderUse") if isinstance(contract.get("builderUse"), dict) else {}
        builder_use.update({
            "safeForFlowOrdering": bool(slot.get("source") == "page_structure.flow" and not spanning),
            "requiresPositionedFrame": placement in {"positioned-text-frame", "spanning-text-frame"},
            "requiresVisualPlacement": placement in {"floating-visual", "inline-visual"},
        })
        contract["builderUse"] = builder_use

        word = row.get("wordParagraph") if isinstance(row.get("wordParagraph"), dict) else {}
        geometry = word.get("geometry") if isinstance(word.get("geometry"), dict) else {}
        geometry["bboxPt"] = box
        geometry["columnBoxPt"] = _box(list((contract.get("column") or {}).get("box", {}).values())) if isinstance((contract.get("column") or {}).get("box"), dict) and all(key in (contract.get("column") or {}).get("box", {}) for key in ("x0", "y0", "x1", "y1")) else geometry.get("columnBoxPt")
        geometry["source"] = "page_structure-slot+pdf-typography"
        word["geometry"] = geometry
        word["placement"] = placement
        word["pageColumns"] = {
            "layoutMode": page.get("layout_mode"),
            "columns": [dict(item) for item in page.get("columns", []) or [] if isinstance(item, dict)],
            "columnCount": len(page.get("columns", []) or []) if page.get("columns") else 1,
            "source": "page_structure-consume-only",
        }
        contract["wordParagraph"] = word
        row["wordParagraph"] = word
        row["layoutContract"] = contract
        locked += 1

    spine["layoutOrderBySlot"] = order_by_slot
    rows = list(spine.get("rows", []) or [])
    rows.sort(key=lambda row: (
        int((row.get("layout") or {}).get("page") or 0),
        1000000 if (row.get("layout") or {}).get("wordFlowOrder") is None else int((row.get("layout") or {}).get("wordFlowOrder")),
        int(row.get("markdownOrder") or 0),
    ))
    spine["rows"] = rows
    spine["linesFirstConsumeOnly"] = {
        "lockedRowCount": locked,
        "inheritedFallbackRowCount": inherited,
        "pageStructureSlotCount": len(slots),
        "policy": (
            "page_structure owns page/slot/order/column/placement in LINES_FIRST; "
            "Markdown/PDF spine, DOCX donor and the ordinary page-layout module may contribute content, typography and native donors only."
        ),
    }
    summary = spine.setdefault("summary", {})
    summary["linesFirstConsumeOnly"] = deepcopy(spine["linesFirstConsumeOnly"])
    return spine


def main() -> int:
    args = build_parser().parse_args()
    pdf = args.pdf.resolve()
    source = args.source.resolve()
    lines = args.lines.resolve() if args.lines else None
    output = args.output.resolve()
    mode = args.mode or ("witness" if lines is not None else "off")

    for path, label in ((pdf, "PDF"), (source, "Mathpix source")):
        if not path.exists():
            raise FileNotFoundError(f"{label} not found: {path}")
    if lines is not None and not lines.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {lines}")
    if mode in {"witness", "lines-first"} and lines is None:
        raise ValueError(f"--mode {mode} requires --lines")

    import pdf_word_reconstructor.cli as recon_cli
    import pdf_word_reconstructor.page_structure as page_structure_module
    from pdf_word_canonical_pipeline.pipeline import main as canonical_pipeline_main

    # OFF has no patch. WITNESS uses the shared witness behavior. LINES_FIRST is
    # intentionally isolated here: build the normal witness structure first, then
    # apply only the experimental hierarchy-led transformation before downstream.
    if mode != "off":
        original_build_page_structure = page_structure_module.build_page_structure

        def lines_reconciled_build_page_structure(
            pdf_analysis,
            work_dir,
            asset_dir,
            reference_docx=None,
            external_asset_paths=None,
            equation_donor_path=None,
            mathpix_lines_path=None,
        ):
            result = original_build_page_structure(
                pdf_analysis,
                work_dir,
                asset_dir,
                reference_docx=reference_docx,
                external_asset_paths=external_asset_paths,
                equation_donor_path=equation_donor_path,
                mathpix_lines_path=lines,
                mathpix_lines_mode="witness",
            )

            lines_first_summary = _apply_lines_first_hierarchy(result) if mode == "lines-first" else None

            evidence = {
                "mode": mode,
                "mathpixLineLayoutMap": deepcopy(result.get("mathpixLineLayoutMap")),
                "mathpixLinesSummary": deepcopy(result.get("mathpixLinesSummary")),
                "linesFirstHierarchySummary": deepcopy(lines_first_summary),
                "pages": [
                    {
                        "page": page.get("page"),
                        "mathpixLinePageMap": deepcopy(page.get("mathpixLinePageMap")),
                    }
                    for page in result.get("pages", []) or []
                    if page.get("mathpixLinePageMap") is not None
                ],
                "policy": (
                    "Lines evidence is internal to page_structure. WITNESS is unchanged. "
                    "LINES_FIRST is isolated in this wrapper and uses explicit Lines hierarchy before geometry: "
                    "semantic role and sibling order follow parent/children relations; PDF geometry remains authoritative; "
                    "the downstream production schema remains unchanged."
                ),
            }
            evidence_path = Path(work_dir) / "MATHPIX_LINES_PAGE_STRUCTURE_EVIDENCE.json"
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            return _strip_lines_only_interface_fields(result)

        recon_cli.build_page_structure = lines_reconciled_build_page_structure

        if mode == "lines-first":
            original_build_page_layout_spine = recon_cli.build_page_layout_spine

            def lines_first_build_page_layout_spine(
                markdown_pdf_spine,
                page_structure,
                docx_donor_map=None,
            ):
                spine = original_build_page_layout_spine(markdown_pdf_spine, page_structure, docx_donor_map)
                return _apply_lines_first_layout_consume_only(spine, page_structure)

            recon_cli.build_page_layout_spine = lines_first_build_page_layout_spine

    argv = [
        "fidelity",
        "--pdf", str(pdf),
        "--source", str(source),
        "--pages", args.pages,
        "--output", str(output),
        "--calibration", args.calibration,
    ]
    if args.strict_page_count:
        argv.append("--strict-page-count")
    if args.no_render:
        argv.append("--no-render")

    output.mkdir(parents=True, exist_ok=True)
    display_mode = {
        "off": "LINES_OFF",
        "witness": "LINES_WITNESS",
        "lines-first": "LINES_FIRST",
    }[mode]
    manifest = {
        "mode": display_mode,
        "pdf": str(pdf),
        "source": str(source),
        "lines": str(lines) if lines is not None else None,
        "pages": args.pages,
        "calibration": args.calibration,
        "contract": {
            "linesScope": "inside page_structure plus LINES_FIRST consume-only layout overlay",
            "downstreamSchema": "unchanged",
            "downstreamModules": "unchanged",
            "renderer": "unchanged",
            "canonicalCleanup": "unchanged",
            "linesFirstPriority": (
                "Lines hierarchy leads semantic role and sibling order. page_structure then owns page/slot/order/column/placement; "
                "the ordinary page-layout stage may contribute content, PDF typography and native donors but cannot supersede exact page_structure slots."
                if mode == "lines-first"
                else None
            ),
            "futureSchemaEvolution": (
                "Still pending: audit Lines-only information that cannot be expressed in the current general schema and "
                "promote only proven general properties through an explicit schema revision."
            ),
        },
    }
    (output / "LINES_AB_MODE.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nFIDELITY MODE: {display_mode}")
    print(f"PDF    : {pdf}")
    print(f"SOURCE : {source}")
    print(f"LINES  : {lines if lines is not None else 'OFF'}")
    print(f"PAGES  : {args.pages}")
    if mode == "lines-first":
        print("PRIORITY: Lines hierarchy -> page_structure authority -> PDF typography -> Markdown/DOCX content/donors")
        print("LINES_FIRST: page_layout_spine consume-only for exact page_structure slots")
    else:
        print("CONTRACT: downstream interface and modules stay unchanged")
    return int(canonical_pipeline_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
