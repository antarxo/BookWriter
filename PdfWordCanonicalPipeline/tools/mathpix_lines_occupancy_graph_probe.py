from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_word_reconstructor.lines_occupancy_graph import build_lines_occupancy_graph


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a Lines-only occupancy graph without Word rendering.")
    parser.add_argument("--lines", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    source = args.lines.resolve()
    if not source.exists():
        raise FileNotFoundError(f"Mathpix Lines not found: {source}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    graph = build_lines_occupancy_graph(source)
    graph_path = output / "LINES_OCCUPANCY_GRAPH.json"
    graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MODE: MATHPIX_LINES_OCCUPANCY_GRAPH_V02")
    print("PDF INPUT       : OFF")
    print("WORD RENDERING  : OFF")
    print("LINES ONLY      : ON")
    print("SPATIAL LAYER   : TOP-LEVEL ROOT OCCUPANCY")
    print("NESTED LAYER    : HIERARCHY ONLY")
    print("TOP ATOMS       : INCLUDED")
    print("ROLE CANDIDATES : DIAGNOSTIC")
    print(f"PAGES           : {graph['summary']['pageCount']}")
    print(f"SPATIAL NODES   : {graph['summary']['spatialNodeCount']}")
    for page in graph.get('pages') or []:
        print(
            f"PAGE {page.get('page')}: roots={page.get('topLevelRootCount')} "
            f"spatial={len(page.get('spatialNodes') or [])} roles={page.get('roleCounts')}"
        )
    print(f"OUTPUT          : {graph_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
