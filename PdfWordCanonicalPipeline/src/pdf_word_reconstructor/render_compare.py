from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat


def count_docx_pages_with_word(docx_path: Path) -> tuple[int, str]:
    """Open a DOCX in Word, repaginate, and return Word's page count.

    This is the fast calibration probe. It deliberately does not export PDF; PDF
    rendering is reserved for candidates whose page count is worth fidelity
    comparison.
    """
    docx_path = docx_path.resolve()
    if not sys.platform.startswith("win"):
        raise RuntimeError("Word page-count probe requires Microsoft Word on Windows.")

    com_initialized = False
    try:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except Exception as exc:
            raise RuntimeError("Word page-count probe requires Microsoft Word + pywin32.") from exc

        pythoncom.CoInitialize()
        com_initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = None
        try:
            document = word.Documents.Open(str(docx_path), ReadOnly=True, AddToRecentFiles=False)
            document.Repaginate()
            page_count = max(1, int(document.ComputeStatistics(2)))
            return page_count, "Microsoft Word page-count"
        finally:
            if document is not None:
                document.Close(False)
            word.Quit()
    finally:
        if com_initialized:
            pythoncom.CoUninitialize()


def probe_font_scale_interval_with_word(
    docx_path: Path,
    expected_pages: int,
    *,
    min_scale: float = 0.60,
    max_scale: float = 1.00,
    max_steps: int = 14,
) -> dict[str, Any]:
    """Find a font scale by directed interval search in one Word session.

    This is deliberately stricter than a pre-baked scale list. Scale 1.0 is the
    PDF-derived typography estimate and therefore the ceiling. When it overflows,
    the first next value is the middle of [min_scale, 1.0], then each remaining
    interval is halved.
    """
    docx_path = docx_path.resolve()
    if not sys.platform.startswith("win"):
        raise RuntimeError("Word font-scale sandbox requires Microsoft Word on Windows.")

    com_initialized = False
    try:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except Exception as exc:
            raise RuntimeError("Word font-scale sandbox requires Microsoft Word + pywin32.") from exc

        pythoncom.CoInitialize()
        com_initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = None
        try:
            document = word.Documents.Open(str(docx_path), ReadOnly=False, AddToRecentFiles=False)
            base_sizes: list[tuple[Any, float]] = []
            for story in document.StoryRanges:
                current = story
                while current is not None:
                    for paragraph in current.Paragraphs:
                        try:
                            size = float(paragraph.Range.Font.Size)
                        except Exception:
                            continue
                        if 1.0 <= size <= 80.0:
                            base_sizes.append((paragraph.Range, size))
                    current = current.NextStoryRange

            results: list[dict[str, Any]] = []
            seen: set[float] = set()

            def measure(scale: float) -> dict[str, Any]:
                scale = round(float(scale), 5)
                for row in results:
                    if abs(float(row["scale"]) - scale) < 0.00001:
                        return row
                for run, base_size in base_sizes:
                    try:
                        run.Font.Size = max(1.0, min(80.0, base_size * scale))
                    except Exception:
                        continue
                document.Repaginate()
                pages = max(1, int(document.ComputeStatistics(2)))
                row = {
                    "scale": round(scale, 5),
                    "pages": pages,
                    "page_delta": abs(int(pages) - int(expected_pages)),
                }
                results.append(row)
                seen.add(scale)
                return row

            initial = measure(1.0)
            direction = "none"
            bracket: dict[str, Any] = {}
            if int(initial["pages"]) == int(expected_pages):
                direction = "exact-at-seed"
            elif int(initial["pages"]) > int(expected_pages):
                direction = "lower-font-scale"
                lo = float(min_scale)
                hi = 1.0
                bracket = {"virtual_low_scale": round(lo, 5), "high": initial}
                for _ in range(max(0, int(max_steps))):
                    mid = round((lo + hi) / 2.0, 5)
                    if mid in seen or abs(hi - lo) < 0.0005:
                        break
                    row = measure(mid)
                    if int(row["pages"]) == int(expected_pages):
                        bracket["exact"] = row
                        break
                    if int(row["pages"]) > int(expected_pages):
                        hi = mid
                        bracket["high"] = row
                    else:
                        lo = mid
                        bracket["low"] = row
                if not any(int(row.get("pages") or 0) <= int(expected_pages) for row in results):
                    bracket["status"] = "minimum-scale-still-over-target"
            else:
                direction = "pdf-estimated-maximum-under-target"
                bracket = {
                    "high": initial,
                    "status": "pdf-derived-ceiling-still-under-target",
                    "note": "Scale 1.0 is the PDF-derived ceiling; the sandbox does not invent larger typography.",
                }

            best = min(
                results,
                key=lambda row: (
                    int(row.get("page_delta") or 10**9),
                    abs(float(row.get("scale") or 1.0) - 1.0),
                ),
            )
            exact_rows = [row for row in results if int(row.get("pages") or 0) == int(expected_pages)]
            if exact_rows:
                best = min(exact_rows, key=lambda row: abs(float(row.get("scale") or 1.0) - 1.0))

            return {
                "policy": "word-open-temporary-font-scale-interval-bisection-no-save",
                "expected_pages": int(expected_pages),
                "run_count": len(base_sizes),
                "min_scale": round(float(min_scale), 5),
                "max_scale": round(float(max_scale), 5),
                "max_steps": int(max_steps),
                "direction": direction,
                "bracket": bracket,
                "results": results,
                "best": best,
            }
        finally:
            if document is not None:
                document.Close(False)
            word.Quit()
    finally:
        if com_initialized:
            pythoncom.CoUninitialize()


def probe_typography_settings_with_word(
    docx_path: Path,
    expected_pages: int,
    settings: list[tuple[float, float, float]],
    *,
    seed_index: int,
) -> dict[str, Any]:
    """Probe body-size/gap/line triples inside one Word session.

    The DOCX is a disposable seed. Word is the pagination authority; this probe
    changes paragraph font size, space-before and line spacing without exporting
    PDFs or saving the document.
    """
    docx_path = docx_path.resolve()
    if not sys.platform.startswith("win"):
        raise RuntimeError("Word typography triple probe requires Microsoft Word on Windows.")
    if not settings:
        return {
            "policy": "word-open-typography-triple-probe-no-save",
            "expected_pages": int(expected_pages),
            "seed_index": int(seed_index),
            "results": [],
            "best": None,
        }
    if seed_index < 0 or seed_index >= len(settings):
        raise ValueError(f"seed_index out of range: {seed_index}")

    seed_size, seed_gap, seed_line = settings[seed_index]
    seed_size = max(1.0, float(seed_size))
    seed_gap = max(0.0, float(seed_gap))
    seed_line = float(seed_line)

    com_initialized = False
    try:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except Exception as exc:
            raise RuntimeError("Word typography triple probe requires Microsoft Word + pywin32.") from exc

        pythoncom.CoInitialize()
        com_initialized = True
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = None
        try:
            document = word.Documents.Open(str(docx_path), ReadOnly=False, AddToRecentFiles=False)
            paragraph_records: list[dict[str, Any]] = []
            for paragraph in document.Paragraphs:
                paragraph_format = paragraph.Range.ParagraphFormat
                try:
                    base_font_size = float(paragraph.Range.Font.Size)
                except Exception:
                    base_font_size = 0.0
                try:
                    base_space_before = float(paragraph_format.SpaceBefore)
                except Exception:
                    base_space_before = 0.0
                try:
                    base_line_spacing = float(paragraph_format.LineSpacing)
                except Exception:
                    base_line_spacing = 0.0
                try:
                    base_line_rule = int(paragraph_format.LineSpacingRule)
                except Exception:
                    base_line_rule = None
                paragraph_records.append({
                    "range": paragraph.Range,
                    "format": paragraph_format,
                    "fontSize": base_font_size if 1.0 <= base_font_size <= 80.0 else None,
                    "spaceBefore": max(0.0, base_space_before),
                    "lineSpacing": max(0.0, base_line_spacing),
                    "lineSpacingRule": base_line_rule,
                })

            results: list[dict[str, Any]] = []
            for index, setting in enumerate(settings):
                size, gap, line = float(setting[0]), float(setting[1]), float(setting[2])
                size_ratio = max(0.05, size / seed_size)
                gap_ratio = 0.0 if gap <= 0.0 else (gap / seed_gap if seed_gap > 0.0 else 1.0)
                for record in paragraph_records:
                    font_size = record.get("fontSize")
                    if font_size is not None and float(font_size) >= 4.0:
                        try:
                            record["range"].Font.Size = max(1.0, min(80.0, float(font_size) * size_ratio))
                        except Exception:
                            pass
                    try:
                        record["format"].SpaceBefore = max(0.0, float(record.get("spaceBefore") or 0.0) * gap_ratio)
                    except Exception:
                        pass
                    try:
                        if line > 0.0:
                            record["format"].LineSpacingRule = 5  # wdLineSpaceMultiple
                            record["format"].LineSpacing = word.LinesToPoints(float(line))
                        else:
                            line_spacing = float(record.get("lineSpacing") or 0.0)
                            if line_spacing > 0.0:
                                if record.get("lineSpacingRule") is not None:
                                    record["format"].LineSpacingRule = int(record["lineSpacingRule"])
                                record["format"].LineSpacing = max(1.0, line_spacing * size_ratio)
                    except Exception:
                        pass
                document.Repaginate()
                pages = max(1, int(document.ComputeStatistics(2)))
                results.append({
                    "candidate_index": int(index),
                    "body_size_pt": round(size, 3),
                    "gap_scale": round(gap, 3),
                    "line_spacing_multiple": round(line, 3),
                    "pages": int(pages),
                    "page_delta": abs(int(pages) - int(expected_pages)),
                })

            exact = [row for row in results if int(row["pages"]) == int(expected_pages)]
            if exact:
                best = min(exact, key=lambda row: abs(int(row["candidate_index"]) - int(seed_index)))
            else:
                best = min(results, key=lambda row: (int(row["page_delta"]), abs(int(row["candidate_index"]) - int(seed_index))))
            return {
                "policy": "word-open-typography-triple-probe-no-save",
                "expected_pages": int(expected_pages),
                "seed_index": int(seed_index),
                "seed_setting": {
                    "body_size_pt": round(seed_size, 3),
                    "gap_scale": round(seed_gap, 3),
                    "line_spacing_multiple": round(seed_line, 3),
                },
                "paragraph_count": len(paragraph_records),
                "result_count": len(results),
                "results": results,
                "best": best,
            }
        finally:
            if document is not None:
                document.Close(False)
            word.Quit()
    finally:
        if com_initialized:
            pythoncom.CoUninitialize()


def export_docx_to_pdf(docx_path: Path, pdf_path: Path) -> str:
    """Export with Microsoft Word on Windows, otherwise LibreOffice.

    Returns the renderer name. Raises RuntimeError when no renderer is available.
    """
    docx_path = docx_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform.startswith("win"):
        com_initialized = False
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore

            pythoncom.CoInitialize()
            com_initialized = True
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            document = None
            try:
                document = word.Documents.Open(str(docx_path), ReadOnly=True, AddToRecentFiles=False)
                document.ExportAsFixedFormat(
                    OutputFileName=str(pdf_path),
                    ExportFormat=17,
                    OpenAfterExport=False,
                    OptimizeFor=0,
                    Range=0,
                    Item=0,
                    IncludeDocProps=True,
                    KeepIRM=True,
                    CreateBookmarks=0,
                    DocStructureTags=True,
                    BitmapMissingFonts=True,
                    UseISO19005_1=False,
                )
            finally:
                if document is not None:
                    document.Close(False)
                word.Quit()
            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                raise RuntimeError("Το Microsoft Word δεν παρήγαγε PDF.")
            return "Microsoft Word"
        except Exception as exc:
            word_error = exc
        finally:
            if com_initialized:
                pythoncom.CoUninitialize()
    else:
        word_error = None

    executable = shutil.which("soffice") or shutil.which("libreoffice")
    if executable:
        temp_home = pdf_path.parent / "_lo_profile"
        temp_home.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["HOME"] = str(temp_home)
        cmd = [
            executable,
            "--headless",
            f"-env:UserInstallation=file:///{temp_home.as_posix()}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_path.parent),
            str(docx_path),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
        produced = pdf_path.parent / f"{docx_path.stem}.pdf"
        if produced.exists() and produced != pdf_path:
            if pdf_path.exists():
                pdf_path.unlink()
            produced.replace(pdf_path)
        if completed.returncode != 0 or not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise RuntimeError(
                "Αποτυχία LibreOffice export.\n"
                f"stdout: {completed.stdout}\n"
                f"stderr: {completed.stderr}\n"
                + (f"Word error: {word_error}" if word_error else "")
            )
        return "LibreOffice"

    raise RuntimeError(
        "Δεν βρέθηκε renderer DOCX→PDF. Σε Windows απαιτείται Microsoft Word + pywin32. "
        "Εναλλακτικά εγκατέστησε LibreOffice."
        + (f" Word error: {word_error}" if word_error else "")
    )


def _render_pdf_pages(pdf_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths: list[Path] = []
    scale = dpi / 72.0
    for index, page in enumerate(doc):
        path = out_dir / f"page-{index + 1}.png"
        page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(path)
        paths.append(path)
    return paths


def _source_page_image(source_pdf: Path, page_1based: int, out_path: Path, dpi: int = 150) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(source_pdf)
    page = doc[page_1based - 1]
    scale = dpi / 72.0
    page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(out_path)
    return out_path


def _page_image(document: fitz.Document, page_index: int, dpi: int = 150) -> Image.Image:
    scale = dpi / 72.0
    pixmap = document[page_index].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _mae(image_a: Image.Image, image_b: Image.Image) -> float:
    diff = ImageChops.difference(image_a, image_b)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)



def _smooth_profile(values: list[float], radius: int = 3) -> list[float]:
    if not values:
        return []
    result: list[float] = []
    for i in range(len(values)):
        lo, hi = max(0, i - radius), min(len(values), i + radius + 1)
        result.append(sum(values[lo:hi]) / max(1, hi - lo))
    return result


def _text_projection(image: Image.Image, page_struct: dict[str, Any]) -> tuple[list[float], float | None]:
    """Return a vertical ink profile for the native main-text column.

    Raster figures/equations are painted out using the PDF page map. The remaining
    profile is dominated by paragraph baselines, so calibration can distinguish a
    merely four-page candidate from one whose text actually reaches the same places.
    """
    gray = image.convert("L")
    width, height = gray.size
    page_width = float(page_struct.get("width_pt") or 595.0)
    page_height = float(page_struct.get("height_pt") or 842.0)
    sx, sy = width / page_width, height / page_height
    column = page_struct.get("main_column", {})
    x0_pt = max(0.0, float(column.get("x0", 36.0)))
    x1_pt = min(page_width, float(column.get("x1", page_width - 36.0)))
    y0_pt = max(0.0, float(column.get("y0", 45.0)) - 8.0)
    y1_pt = min(page_height, float(column.get("y1", page_height - 70.0)) + 8.0)
    box = (
        max(0, round(x0_pt * sx)),
        max(0, round(y0_pt * sy)),
        min(width, round(x1_pt * sx)),
        min(height, round(y1_pt * sy)),
    )
    crop = gray.crop(box)
    draw = ImageDraw.Draw(crop)
    for group in page_struct.get("visual_groups", []):
        bbox = group.get("bbox", [0, 0, 0, 0])
        gx0 = round(float(bbox[0]) * sx) - box[0]
        gy0 = round(float(bbox[1]) * sy) - box[1]
        gx1 = round(float(bbox[2]) * sx) - box[0]
        gy1 = round(float(bbox[3]) * sy) - box[1]
        if gx1 > 0 and gy1 > 0 and gx0 < crop.width and gy0 < crop.height:
            draw.rectangle((gx0 - 3, gy0 - 3, gx1 + 3, gy1 + 3), fill=255)

    pixels = crop.load()
    profile: list[float] = []
    active_rows: list[int] = []
    for y in range(crop.height):
        dark = 0
        for x in range(crop.width):
            if pixels[x, y] < 205:
                dark += 1
        density = dark / max(1, crop.width)
        profile.append(density)
        if density >= 0.003:
            active_rows.append(y)
    profile = _smooth_profile(profile, radius=3)
    last_pt = None
    if active_rows:
        last_pt = y0_pt + active_rows[-1] / sy
    return profile, last_pt


def _profile_mae(a: list[float], b: list[float]) -> float:
    count = min(len(a), len(b))
    if count <= 0:
        return 0.0
    return sum(abs(a[i] - b[i]) for i in range(count)) / count


def compare_pdf_to_source(
    source_pdf: Path,
    source_pages: list[int],
    output_pdf: Path,
    compare_dir: Path,
    dpi: int = 150,
    write_images: bool = True,
    page_structure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_doc = fitz.open(source_pdf)
    output_doc = fitz.open(output_pdf)
    output_page_count = len(output_doc)
    if write_images:
        (compare_dir / "source").mkdir(parents=True, exist_ok=True)
        (compare_dir / "output").mkdir(parents=True, exist_ok=True)
    page_results: list[dict[str, Any]] = []
    pair_count = min(len(source_pages), output_page_count)
    boundary_warn_pt = 120.0
    boundary_fail_pt = 220.0
    comparison_stopped_early = False
    early_stop_reason = None

    for index in range(pair_count):
        source_page = source_pages[index]
        with _page_image(source_doc, source_page - 1, dpi=dpi) as src, _page_image(output_doc, index, dpi=dpi) as out:
            if out.size != src.size:
                out = out.resize(src.size, Image.Resampling.LANCZOS)
            if write_images:
                src.save(compare_dir / "source" / f"page-{source_page}.png")
                out.save(compare_dir / "output" / f"page-{index + 1}.png")
            gray_src = src.convert("L")
            gray_out = out.convert("L")
            pixel_mae = _mae(gray_src, gray_out)
            edge_src = gray_src.filter(ImageFilter.FIND_EDGES)
            edge_out = gray_out.filter(ImageFilter.FIND_EDGES)
            edge_mae = _mae(edge_src, edge_out)
            projection_mae = 0.0
            text_end_delta_pt = 0.0
            text_end_delta_signed_pt = 0.0
            source_text_end_pt = None
            output_text_end_pt = None
            if page_structure and index < len(page_structure.get("pages", [])):
                structure_page = page_structure["pages"][index]
                src_profile, src_end = _text_projection(src, structure_page)
                out_profile, out_end = _text_projection(out, structure_page)
                projection_mae = _profile_mae(src_profile, out_profile)
                if src_end is not None and out_end is not None:
                    source_text_end_pt = src_end
                    output_text_end_pt = out_end
                    text_end_delta_signed_pt = out_end - src_end
                    text_end_delta_pt = abs(src_end - out_end)
            if write_images:
                diff = ImageChops.difference(src, out)
                canvas = Image.new("RGB", (src.width * 3, src.height), "white")
                canvas.paste(src, (0, 0))
                canvas.paste(out, (src.width, 0))
                canvas.paste(diff, (src.width * 2, 0))
                canvas.save(compare_dir / f"compare-source{source_page}-output{index + 1}.png")
            page_results.append({
                "source_page": source_page,
                "output_page": index + 1,
                "pixel_mae": round(pixel_mae, 4),
                "edge_mae": round(edge_mae, 4),
                "text_projection_mae": round(projection_mae, 6),
                "source_text_end_pt": round(source_text_end_pt, 3) if source_text_end_pt is not None else None,
                "output_text_end_pt": round(output_text_end_pt, 3) if output_text_end_pt is not None else None,
                "text_end_delta_signed_pt": round(text_end_delta_signed_pt, 3),
                "text_end_delta_pt": round(text_end_delta_pt, 3),
            })
            early_boundary_overflow = float(text_end_delta_signed_pt or 0.0) > boundary_fail_pt
            if (
                not write_images
                and page_structure
                and early_boundary_overflow
                and index <= 2
            ):
                comparison_stopped_early = True
                early_stop_reason = "early-page-boundary-overflow"
                break

    average_pixel = sum(p["pixel_mae"] for p in page_results) / max(1, len(page_results))
    average_edge = sum(p["edge_mae"] for p in page_results) / max(1, len(page_results))
    average_projection = sum(p["text_projection_mae"] for p in page_results) / max(1, len(page_results))
    average_text_end_delta = sum(p["text_end_delta_pt"] for p in page_results) / max(1, len(page_results))
    boundary_warnings = [
        p for p in page_results
        if abs(float(p.get("text_end_delta_signed_pt") or 0.0)) > boundary_warn_pt
    ]
    overflow_warnings = [
        p for p in page_results
        if float(p.get("text_end_delta_signed_pt") or 0.0) > boundary_warn_pt
    ]
    underfill_warnings = [
        p for p in page_results
        if float(p.get("text_end_delta_signed_pt") or 0.0) < -boundary_warn_pt
    ]
    boundary_failures = [
        p for p in page_results
        if float(p.get("text_end_delta_signed_pt") or 0.0) > boundary_fail_pt
    ]
    underfill_failures = [
        p for p in page_results
        if float(p.get("text_end_delta_signed_pt") or 0.0) < -boundary_fail_pt
    ]
    average_overflow_delta = sum(
        max(0.0, float(p.get("text_end_delta_signed_pt") or 0.0))
        for p in page_results
    ) / max(1, len(page_results))
    average_underfill_delta = sum(
        max(0.0, -float(p.get("text_end_delta_signed_pt") or 0.0))
        for p in page_results
    ) / max(1, len(page_results))
    allowed_boundary_failures = max(1, round(pair_count * 0.08))
    page_boundary_pass = bool(
        not comparison_stopped_early
        and len(page_results) == pair_count
        and
        len(boundary_failures) <= allowed_boundary_failures
        and average_overflow_delta <= 160.0
    )
    boundary_penalty = average_text_end_delta * 0.35 + len(boundary_failures) * 15.0 + (500.0 if comparison_stopped_early else 0.0)
    page_penalty = abs(output_page_count - len(source_pages)) * 1000.0
    objective = (
        average_edge
        + average_pixel * 0.10
        + average_projection * 180.0
        + boundary_penalty
        + page_penalty
    )
    return {
        "source_page_count": len(source_pages),
        "output_page_count": output_page_count,
        "paired_pages": pair_count,
        "compared_pages": len(page_results),
        "comparison_stopped_early": comparison_stopped_early,
        "early_stop_reason": early_stop_reason,
        "average_pixel_mae": round(average_pixel, 4),
        "average_edge_mae": round(average_edge, 4),
        "average_text_projection_mae": round(average_projection, 6),
        "average_text_end_delta_pt": round(average_text_end_delta, 3),
        "average_overflow_delta_pt": round(average_overflow_delta, 3),
        "average_underfill_delta_pt": round(average_underfill_delta, 3),
        "page_boundary_pass": page_boundary_pass,
        "page_boundary_policy": {
            "warn_pt": boundary_warn_pt,
            "fail_pt": boundary_fail_pt,
            "allowed_failures": allowed_boundary_failures,
            "hard_fail_direction": "positive-overflow",
            "underfill_policy": "diagnostic-warning-not-publication-blocker",
            "max_average_overflow_delta_pt": 160.0,
            "early_stop": "first-three-pages-positive-overflow-only",
        },
        "page_boundary_warning_count": len(boundary_warnings),
        "page_boundary_failure_count": len(boundary_failures),
        "page_overflow_warning_count": len(overflow_warnings),
        "page_underfill_warning_count": len(underfill_warnings),
        "page_underfill_failure_count": len(underfill_failures),
        "first_page_boundary_failure": boundary_failures[0] if boundary_failures else None,
        "first_page_underfill_failure": underfill_failures[0] if underfill_failures else None,
        "boundary_penalty": round(boundary_penalty, 4),
        "page_count_penalty": page_penalty,
        "objective": round(objective, 4),
        "pages": page_results,
    }
