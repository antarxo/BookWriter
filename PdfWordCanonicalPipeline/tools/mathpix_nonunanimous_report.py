from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ACCEPTED = {"exact", "strong", "usable"}


def _status(payload: Any) -> str:
    if isinstance(payload, dict):
        return str(payload.get("status") or "unknown")
    return "unknown"


def _score(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("score")
    return None


def _find_witness_payload(row: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            return value
    lowered = {str(k).lower(): k for k in row}
    for name in names:
        needle = name.lower()
        for lk, original in lowered.items():
            if needle in lk and isinstance(row.get(original), dict):
                return row[original]
    return {}


def main() -> int:
    p = argparse.ArgumentParser(description="Explain all non-unanimous items from MATHPIX_FULL_EVIDENCE_BENCHMARK.json")
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--output", type=Path)
    args = p.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    items = list(data.get("items") or [])
    rows: list[dict[str, Any]] = []
    patterns: Counter[str] = Counter()
    patterns_by_type: dict[str, Counter[str]] = defaultdict(Counter)

    for row in items:
        witness_count = int(row.get("witnessCount") or 0)
        if witness_count == 3:
            continue
        mmd = _find_witness_payload(row, ("mmd", "mmdMatch", "fullMmd", "mmdWitness"))
        docx = _find_witness_payload(row, ("docx", "docxMatch", "docxWitness"))
        lines = _find_witness_payload(row, ("lines", "lineMatch", "linesMatch", "linesWitness"))
        statuses = {
            "MMD": _status(mmd),
            "DOCX": _status(docx),
            "LINES": _status(lines),
        }
        accepted = tuple(name for name, status in statuses.items() if status in ACCEPTED)
        rejected = tuple(name for name, status in statuses.items() if status not in ACCEPTED)
        pattern = "+".join(accepted) + (" | miss:" + "+".join(rejected) if rejected else "")
        kind = str(row.get("type") or "unknown")
        patterns[pattern] += 1
        patterns_by_type[kind][pattern] += 1
        rows.append({
            "markdownId": row.get("markdownId"),
            "type": kind,
            "witnessCount": witness_count,
            "pattern": pattern,
            "statuses": statuses,
            "scores": {"MMD": _score(mmd), "DOCX": _score(docx), "LINES": _score(lines)},
            "textPreview": row.get("textPreview"),
            "rawWitnessPayloads": {"MMD": mmd, "DOCX": docx, "LINES": lines},
        })

    print("\nNON-UNANIMOUS MATHPIX WITNESS CASES")
    print(f"Total items: {len(items)}")
    print(f"Non-unanimous: {len(rows)}")
    print("\nPATTERNS")
    for pattern, count in patterns.most_common():
        print(f"  {count:2d}  {pattern}")
    print("\nBY TYPE")
    for kind in sorted(patterns_by_type):
        chunks = ", ".join(f"{p}={n}" for p, n in patterns_by_type[kind].most_common())
        print(f"  {kind}: {chunks}")
    print("\nITEMS")
    for row in rows:
        s = row["statuses"]
        sc = row["scores"]
        print(
            f"  {row['markdownId']} {row['type']} witnesses={row['witnessCount']} "
            f"MMD={s['MMD']}({sc['MMD']}) DOCX={s['DOCX']}({sc['DOCX']}) LINES={s['LINES']}({sc['LINES']})"
        )
        print(f"    {str(row.get('textPreview') or '')[:160]}")

    out = {
        "sourceReport": str(args.report),
        "nonUnanimousCount": len(rows),
        "patterns": dict(patterns),
        "patternsByType": {k: dict(v) for k, v in patterns_by_type.items()},
        "items": rows,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nReport: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
