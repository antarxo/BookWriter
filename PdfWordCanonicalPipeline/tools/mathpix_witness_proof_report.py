from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ACCEPTED = {"exact", "strong", "usable"}


def _find_witness_payload(row: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    for name in names:
        value = row.get(name)
        if isinstance(value, dict):
            return value
    lowered = {str(k).lower(): k for k in row}
    for name in names:
        needle = name.lower()
        for lk, original in lowered.items():
            value = row.get(original)
            if needle in lk and isinstance(value, dict):
                return value
    return {}


def _status(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "unknown")


def _score(payload: dict[str, Any]) -> Any:
    return payload.get("score")


def _target_identity(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "targetIndex", "paragraphIndex", "paragraphId", "page", "pageStart", "pageEnd",
        "line", "lineStart", "lineEnd", "id", "lineIds", "bbox", "region", "kind", "type",
        "textPreview",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def main() -> int:
    p = argparse.ArgumentParser(description="Produce complete positive and negative witness evidence for every Mathpix item")
    p.add_argument("--report", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    data = json.loads(args.report.read_text(encoding="utf-8"))
    items = list(data.get("items") or [])

    proof_rows: list[dict[str, Any]] = []
    consensus_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = defaultdict(Counter)

    for row in items:
        mmd = _find_witness_payload(row, ("mmd", "mmdMatch", "fullMmd", "mmdWitness"))
        docx = _find_witness_payload(row, ("docx", "docxMatch", "docxWitness"))
        lines = _find_witness_payload(row, ("lines", "lineMatch", "linesMatch", "linesWitness"))
        payloads = {"MMD": mmd, "DOCX": docx, "LINES": lines}
        statuses = {name: _status(payload) for name, payload in payloads.items()}
        accepted = [name for name, status in statuses.items() if status in ACCEPTED]
        rejected = [name for name, status in statuses.items() if status not in ACCEPTED]
        witness_count = len(accepted)

        if witness_count == 3:
            consensus_class = "unanimous-3-of-3"
        elif witness_count == 2:
            consensus_class = "confirmed-2-of-3"
        elif witness_count == 1:
            consensus_class = "single-witness-only"
        else:
            consensus_class = "no-confirming-witness"

        pattern = "+".join(accepted) + (" | miss:" + "+".join(rejected) if rejected else "")
        kind = str(row.get("type") or "unknown")
        consensus_counts[consensus_class] += 1
        pattern_counts[pattern] += 1
        by_type[kind][pattern] += 1

        witness_proof = {}
        for name, payload in payloads.items():
            witness_proof[name] = {
                "accepted": statuses[name] in ACCEPTED,
                "status": statuses[name],
                "score": _score(payload),
                "target": _target_identity(payload),
                "raw": payload,
            }

        proof_rows.append({
            "markdownId": row.get("markdownId"),
            "type": kind,
            "orderIndex": row.get("orderIndex"),
            "textPreview": row.get("textPreview"),
            "consensusClass": consensus_class,
            "witnessCount": witness_count,
            "pattern": pattern,
            "statuses": statuses,
            "witnessProof": witness_proof,
        })

    unanimous = [row for row in proof_rows if row["consensusClass"] == "unanimous-3-of-3"]
    non_unanimous = [row for row in proof_rows if row["consensusClass"] != "unanimous-3-of-3"]
    weak = [row for row in proof_rows if row["witnessCount"] < 2]

    output = {
        "sourceReport": str(args.report),
        "itemCount": len(proof_rows),
        "proofPolicy": {
            "acceptedStatuses": sorted(ACCEPTED),
            "positiveProof": "each accepted witness retains status, score, target identity and raw benchmark payload",
            "negativeProof": "each rejected witness retains status, score, target identity when present and raw benchmark payload",
            "unanimousProof": "all three witness payloads are preserved even when all agree",
            "noSilentCollapse": True,
        },
        "consensusCounts": dict(consensus_counts),
        "patternCounts": dict(pattern_counts),
        "patternsByType": {kind: dict(counts) for kind, counts in by_type.items()},
        "unanimousItems": unanimous,
        "nonUnanimousItems": non_unanimous,
        "fewerThanTwoWitnesses": weak,
        "allItems": proof_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\nMATHPIX COMPLETE WITNESS PROOF REPORT")
    print(f"All items: {len(proof_rows)}")
    print(f"Unanimous 3-of-3: {len(unanimous)}")
    print(f"Non-unanimous: {len(non_unanimous)}")
    print(f"Fewer than 2 confirming witnesses: {len(weak)}")
    print("\nCONSENSUS CLASSES")
    for name, count in consensus_counts.most_common():
        print(f"  {count:2d}  {name}")
    print("\nPATTERNS")
    for pattern, count in pattern_counts.most_common():
        print(f"  {count:2d}  {pattern}")
    print("\nUNANIMOUS ITEMS (proof retained)")
    for row in unanimous:
        s = row["statuses"]
        scores = {name: row["witnessProof"][name]["score"] for name in ("MMD", "DOCX", "LINES")}
        print(
            f"  {row['markdownId']} {row['type']} "
            f"MMD={s['MMD']}({scores['MMD']}) DOCX={s['DOCX']}({scores['DOCX']}) LINES={s['LINES']}({scores['LINES']})"
        )
    print("\nNON-UNANIMOUS ITEMS")
    for row in non_unanimous:
        s = row["statuses"]
        scores = {name: row["witnessProof"][name]["score"] for name in ("MMD", "DOCX", "LINES")}
        print(
            f"  {row['markdownId']} {row['type']} {row['pattern']} "
            f"MMD={s['MMD']}({scores['MMD']}) DOCX={s['DOCX']}({scores['DOCX']}) LINES={s['LINES']}({scores['LINES']})"
        )
    print(f"\nReport: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
