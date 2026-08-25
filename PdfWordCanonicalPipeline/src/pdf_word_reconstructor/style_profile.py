from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_style_profile(pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    by_size: Counter[float] = Counter()
    by_font: Counter[str] = Counter()
    by_color: Counter[str] = Counter()
    combinations: Counter[tuple[str, float, str, int]] = Counter()
    per_page: dict[int, Counter[float]] = defaultdict(Counter)

    for page in pdf_analysis["pages"]:
        for region in page["regions"]:
            if region["type"] != "text":
                continue
            for line in region["lines"]:
                for span in line["spans"]:
                    weight = max(1, len(span["text"].strip()))
                    size = span["size_pt"]
                    font = span["font"]
                    color = span["color"]
                    flags = span["flags"]
                    by_size[size] += weight
                    by_font[font] += weight
                    by_color[color] += weight
                    combinations[(font, size, color, flags)] += weight
                    per_page[page["page"]][size] += weight

    sizes = sorted(by_size.items(), key=lambda kv: (-kv[1], kv[0]))
    body_size = sizes[0][0] if sizes else None
    return {
        "inferred_body_font_size_pt": body_size,
        "font_sizes": [{"size_pt": k, "weighted_chars": v} for k, v in sizes],
        "fonts": [{"font": k, "weighted_chars": v} for k, v in by_font.most_common()],
        "colors": [{"color": k, "weighted_chars": v} for k, v in by_color.most_common()],
        "combinations": [
            {"font": k[0], "size_pt": k[1], "color": k[2], "flags": k[3], "weighted_chars": v}
            for k, v in combinations.most_common()
        ],
        "per_page_font_sizes": {
            str(page): [{"size_pt": size, "weighted_chars": count} for size, count in counter.most_common()]
            for page, counter in per_page.items()
        },
        "note": "Τα μεγέθη είναι οι ονομαστικές τιμές του PDF. Η τελική τιμή Word θα χρειαστεί render/calibration.",
    }
