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

        matched: dict[int, dict[str, Any]] = {}
        groups: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

        for index, item in enumerate(flow):
            item_box = _box(item.get("bbox"))
            if item_box is None:
                continue
            record = _best_record_match(item_box, records)
            if record is None:
                continue
            matched[index] = record
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
            "linesScope": "inside page_structure only",
            "downstreamSchema": "unchanged",
            "downstreamModules": "unchanged",
            "renderer": "unchanged",
            "canonicalCleanup": "unchanged",
            "linesFirstPriority": (
                "Lines hierarchy leads semantic role and sibling order. Column ownership is refined only when PDF already "
                "confirms a two-column page. Unrelated containers are never globally reordered."
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
        print("PRIORITY: Lines hierarchy -> PDF physical authority -> Markdown/DOCX content/donors")
        print("LINES_FIRST: no forced Word columns; no global cross-container flow sort")
    else:
        print("CONTRACT: downstream interface and modules stay unchanged")
    return int(canonical_pipeline_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
