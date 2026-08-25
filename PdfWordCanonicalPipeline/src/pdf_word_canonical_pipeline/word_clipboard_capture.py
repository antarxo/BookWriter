from __future__ import annotations

"""Robust Microsoft Word -> PNG clipboard capture helpers.

HF24 isolates a failure that previous builds incorrectly attributed to OLE or
DrawingML semantics.  Word's CopyAsPicture can place a perfectly usable bitmap
on the Windows clipboard while PowerPoint's PasteSpecial(Enhanced Metafile)
fails, or a long PowerPoint/clipboard session can become poisoned after one COM
exception.

This module therefore makes Word's raster clipboard representation the primary
browser surrogate. HF25 additionally renders Word's CF_ENHMETAFILE clipboard
representation directly with Windows GDI before any Office-to-Office paste.
PowerPoint is only a last fallback supplied by callers.
"""

import ctypes
import os
import time
from pathlib import Path
from typing import Any


def _clipboard_sequence_number() -> int:
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return 0


def clear_windows_clipboard() -> bool:
    """Best-effort clipboard clear so stale content cannot masquerade as success."""
    if os.name != "nt":
        return False
    try:
        import win32clipboard  # type: ignore
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception:
        return False



CF_ENHMETAFILE = 14
BI_RGB = 0
DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def clipboard_enhmetafile_to_png(
    png_path: Path,
    *,
    width_pt: float = 0.0,
    height_pt: float = 0.0,
    scale_px_per_pt: float = 4.0,
    max_px: int = 4096,
) -> dict[str, Any]:
    """Render CF_ENHMETAFILE from the Windows clipboard directly with GDI.

    Word CopyAsPicture often places a correct enhanced metafile even when it does
    not publish CF_DIB.  HF24 then had to route that object through Word-PDF or
    PowerPoint.  HF25 keeps the object inside the Windows graphics stack: the EMF
    is played into a 32-bit DIB at the *Word-reported* display extent and saved as
    PNG.  This avoids PowerPoint reinterpretation of group transforms/patterns.
    """
    result: dict[str, Any] = {
        "ok": False,
        "backend": "word-clipboard-enhmetafile-gdi",
        "widthPx": 0,
        "heightPx": 0,
        "bytes": 0,
        "error": "",
    }
    if os.name != "nt":
        result["error"] = "non-windows"
        return result
    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        result["error"] = f"pillow-unavailable: {exc}"
        return result

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_int
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = ctypes.c_int
    user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
    user32.IsClipboardFormatAvailable.restype = ctypes.c_int
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = ctypes.c_int
    gdi32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    gdi32.PlayEnhMetaFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT)]
    gdi32.PlayEnhMetaFile.restype = ctypes.c_int

    # Word points map naturally to PDF/Office geometry.  Render at ~288 dpi by
    # default, but cap large objects so diagnostics cannot allocate huge buffers.
    w_pt = max(1.0, float(width_pt or 0.0))
    h_pt = max(1.0, float(height_pt or 0.0))
    scale = max(1.0, float(scale_px_per_pt or 4.0))
    px_w = max(64, int(round(w_pt * scale)))
    px_h = max(64, int(round(h_pt * scale)))
    if max(px_w, px_h) > max_px:
        factor = float(max_px) / float(max(px_w, px_h))
        px_w = max(64, int(round(px_w * factor)))
        px_h = max(64, int(round(px_h * factor)))

    opened = False
    hdc = hbmp = old_obj = None
    try:
        # Clipboard can be briefly busy after Word publishes CopyAsPicture.
        for _ in range(20):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.02)
        if not opened:
            result["error"] = "open-clipboard-failed"
            return result
        if not user32.IsClipboardFormatAvailable(CF_ENHMETAFILE):
            result["error"] = "cf-enhmetafile-unavailable"
            return result
        hemf = user32.GetClipboardData(CF_ENHMETAFILE)
        if not hemf:
            result["error"] = "get-clipboard-enhmetafile-failed"
            return result

        hdc = gdi32.CreateCompatibleDC(None)
        if not hdc:
            result["error"] = "create-compatible-dc-failed"
            return result
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = px_w
        bmi.bmiHeader.biHeight = -px_h  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0)
        if not hbmp or not bits:
            result["error"] = "create-dib-section-failed"
            return result
        old_obj = gdi32.SelectObject(hdc, hbmp)
        byte_count = px_w * px_h * 4
        ctypes.memset(bits, 255, byte_count)  # opaque white BGRX background
        rect = _RECT(0, 0, px_w, px_h)
        if not gdi32.PlayEnhMetaFile(hdc, hemf, ctypes.byref(rect)):
            result["error"] = "play-enhmetafile-failed"
            return result
        raw = ctypes.string_at(bits, byte_count)
        image = Image.frombuffer("RGB", (px_w, px_h), raw, "raw", "BGRX", 0, 1)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(png_path, format="PNG", optimize=True)
        if not png_path.exists() or png_path.stat().st_size <= 100:
            result["error"] = "png-write-failed"
            return result
        result.update({
            "ok": True,
            "widthPx": px_w,
            "heightPx": px_h,
            "bytes": int(png_path.stat().st_size),
            "error": "",
        })
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        if old_obj and hdc:
            try:
                gdi32.SelectObject(hdc, old_obj)
            except Exception:
                pass
        if hbmp:
            try:
                gdi32.DeleteObject(hbmp)
            except Exception:
                pass
        if hdc:
            try:
                gdi32.DeleteDC(hdc)
            except Exception:
                pass
        if opened:
            try:
                user32.CloseClipboard()
            except Exception:
                pass

def clipboard_image_to_png(
    png_path: Path,
    *,
    timeout_s: float = 1.8,
    poll_s: float = 0.05,
    sequence_before: int | None = None,
) -> dict[str, Any]:
    """Poll Windows clipboard and save a real raster image directly to PNG.

    Pillow ImageGrab reads CF_DIB/bitmap data placed by Word CopyAsPicture.  It
    deliberately bypasses PowerPoint and therefore avoids PasteSpecial format
    negotiation, which was the dominant HF23 OLE failure stage.
    """
    result: dict[str, Any] = {
        "ok": False,
        "backend": "word-clipboard-bitmap",
        "polls": 0,
        "sequenceBefore": int(sequence_before or 0),
        "sequenceAfter": 0,
        "widthPx": 0,
        "heightPx": 0,
        "bytes": 0,
        "error": "",
    }
    if os.name != "nt":
        result["error"] = "non-windows"
        return result
    try:
        from PIL import Image, ImageGrab  # type: ignore
    except Exception as exc:
        result["error"] = f"pillow-imagegrab-unavailable: {exc}"
        return result

    deadline = time.monotonic() + max(0.2, float(timeout_s))
    last_error = ""
    while time.monotonic() < deadline:
        result["polls"] += 1
        try:
            data = ImageGrab.grabclipboard()
            if isinstance(data, Image.Image):
                image = data.convert("RGBA")
                png_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(png_path, format="PNG", optimize=True)
                if png_path.exists() and png_path.stat().st_size > 100:
                    result.update({
                        "ok": True,
                        "sequenceAfter": _clipboard_sequence_number(),
                        "widthPx": int(image.width),
                        "heightPx": int(image.height),
                        "bytes": int(png_path.stat().st_size),
                        "error": "",
                    })
                    return result
            elif isinstance(data, list):
                last_error = "clipboard-file-list-not-image"
            elif data is None:
                last_error = "clipboard-image-not-ready"
            else:
                last_error = f"unsupported-clipboard-type:{type(data).__name__}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(max(0.01, float(poll_s)))

    result["sequenceAfter"] = _clipboard_sequence_number()
    result["error"] = last_error or "clipboard-image-timeout"
    return result


def copy_as_picture_to_png(
    copy_callable,
    png_path: Path,
    *,
    attempts: int = 3,
    timeout_s: float = 1.8,
    settle_s: float = 0.06,
) -> dict[str, Any]:
    """Execute a Word CopyAsPicture action and capture its bitmap with retries."""
    audit: dict[str, Any] = {
        "ok": False,
        "backend": "word-clipboard-bitmap",
        "attempts": 0,
        "copyErrors": [],
        "captureErrors": [],
        "widthPx": 0,
        "heightPx": 0,
        "bytes": 0,
    }
    for attempt in range(1, max(1, int(attempts)) + 1):
        audit["attempts"] = attempt
        clear_windows_clipboard()
        sequence_before = _clipboard_sequence_number()
        try:
            copy_callable()
        except Exception as exc:
            audit["copyErrors"].append(f"attempt-{attempt}:{type(exc).__name__}: {exc}")
            time.sleep(0.08 * attempt)
            continue
        time.sleep(max(0.01, float(settle_s)))
        capture = clipboard_image_to_png(
            png_path,
            timeout_s=timeout_s,
            sequence_before=sequence_before,
        )
        if capture.get("ok"):
            audit.update({
                "ok": True,
                "widthPx": int(capture.get("widthPx") or 0),
                "heightPx": int(capture.get("heightPx") or 0),
                "bytes": int(capture.get("bytes") or 0),
                "clipboardPolls": int(capture.get("polls") or 0),
                "sequenceBefore": int(capture.get("sequenceBefore") or 0),
                "sequenceAfter": int(capture.get("sequenceAfter") or 0),
            })
            return audit
        audit["captureErrors"].append(f"attempt-{attempt}:{capture.get('error') or 'unknown'}")
        time.sleep(0.08 * attempt)
    return audit
