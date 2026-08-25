from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def _pick_file(title: str, filetypes: list[tuple[str, str]]) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askopenfilename(title=title, filetypes=filetypes)
        root.destroy()
        return Path(selected) if selected else None
    except Exception:
        return None


def _ask_text(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def _safe_name(value: str) -> str:
    value = re.sub(r"[^\w\u0370-\u03ff-]+", "_", value, flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "selected"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the canonical pipeline reconstruction stage for selected PDF/DOCX files")
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--docx", type=Path)
    parser.add_argument("--pages", help="PDF page range, for example 17-42 or 1-16")
    parser.add_argument("--output-root", type=Path, default=Path("output") / "selected_runs")
    parser.add_argument("--calibration", choices=("none", "fast", "full"), default="none")
    parser.add_argument(
        "--strict-page-count",
        action="store_true",
        help="Only publish when the output page count exactly matches the selected PDF range",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    pdf = args.pdf
    if pdf is None:
        pdf = _pick_file("Επίλεξε το αρχικό/reference PDF", [("PDF files", "*.pdf"), ("All files", "*.*")])
    if pdf is None:
        pdf = Path(_ask_text("PDF path", "")).expanduser()

    docx = args.docx
    if docx is None:
        docx = _pick_file("Επίλεξε το DOCX του μετατροπέα", [("Word files", "*.docx"), ("All files", "*.*")])
    if docx is None:
        docx = Path(_ask_text("DOCX path", "")).expanduser()

    pages = args.pages or _ask_text("PDF σελίδες", "1-16")
    pdf = pdf.resolve()
    docx = docx.resolve()
    if not pdf.is_file():
        print(f"Δεν βρέθηκε PDF: {pdf}")
        return 2
    if not docx.is_file():
        print(f"Δεν βρέθηκε DOCX: {docx}")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = _safe_name(f"{pdf.stem}_{pages}_{stamp}")
    output = (args.output_root / run_name).resolve()

    command = [
        sys.executable,
        "-m",
        "pdf_word_reconstructor.cli",
        "--pdf",
        str(pdf),
        "--docx",
        str(docx),
        "--pages",
        pages,
        "--output",
        str(output),
        "--calibration",
        args.calibration,
    ]
    if args.strict_page_count:
        command.append("--strict-page-count")

    print()
    print("Θα τρέξει το reconstruction stage με:")
    print(f"PDF   : {pdf}")
    print(f"DOCX  : {docx}")
    print(f"Pages : {pages} (σελίδες PDF)")
    print(f"Output: {output}")
    print("Mode  : review candidate" if not args.strict_page_count else "Mode  : strict page-count")
    print()

    result = subprocess.run(command)
    print()
    if result.returncode == 0:
        print("Ολοκληρώθηκε.")
    else:
        print(f"Σταμάτησε με κωδικό {result.returncode}. Δες report/log στο output folder.")
    print(f"Output folder: {output}")
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
