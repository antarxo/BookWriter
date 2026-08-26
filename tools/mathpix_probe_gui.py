#!/usr/bin/env python3
"""Windows GUI wrapper for the diagnostic Mathpix Files API probe."""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import requests
import mathpix_probe as probe


class MathpixProbeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Mathpix Lines Probe")
        self.geometry("860x610")
        self.minsize(780, 540)

        self.pdf_var = tk.StringVar()
        self.key_var = tk.StringVar(value=os.environ.get("MATHPIX_APP_KEY", ""))
        self.output_var = tk.StringVar()
        self.output_is_custom = False
        self.eu_var = tk.BooleanVar(value=True)
        self.show_key_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Έτοιμο")
        self.last_output_dir: Path | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._build_ui()
        self.after(120, self._drain_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(7, weight=1)

        ttk.Label(root, text="Mathpix Lines Probe", font=("Segoe UI", 17, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        ttk.Label(root, text="PDF → Mathpix Files API → MMD + lines.json + diagnostics").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 18)
        )

        ttk.Label(root, text="PDF").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(root, textvariable=self.pdf_var).grid(row=2, column=1, sticky="ew", pady=6)
        ttk.Button(root, text="Επιλογή…", command=self._choose_pdf).grid(row=2, column=2, padx=(10, 0), pady=6)

        ttk.Label(root, text="Mathpix APP KEY").grid(row=3, column=0, sticky="w", padx=(0, 10), pady=6)
        self.key_entry = ttk.Entry(root, textvariable=self.key_var, show="•")
        self.key_entry.grid(row=3, column=1, sticky="ew", pady=6)
        ttk.Checkbutton(root, text="Εμφάνιση", variable=self.show_key_var, command=self._toggle_key).grid(
            row=3, column=2, padx=(10, 0), pady=6, sticky="w"
        )

        ttk.Label(root, text="Τελικός φάκελος αποτελεσμάτων").grid(
            row=4, column=0, sticky="w", padx=(0, 10), pady=6
        )
        self.output_entry = ttk.Entry(root, textvariable=self.output_var)
        self.output_entry.grid(row=4, column=1, sticky="ew", pady=6)
        self.output_entry.bind("<KeyRelease>", self._mark_custom_output)
        ttk.Button(root, text="Αλλαγή…", command=self._choose_output).grid(row=4, column=2, padx=(10, 0), pady=6)

        ttk.Label(
            root,
            text="Προεπιλογή: δίπλα στο PDF, ως <όνομα_pdf>_mathpix. Η διαδρομή φαίνεται εδώ πριν τη μετατροπή.",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))

        options = ttk.Frame(root)
        options.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        ttk.Checkbutton(options, text="EU endpoint (δεδομένα εγγράφου στην ΕΕ)", variable=self.eu_var).pack(side="left")

        log_frame = ttk.LabelFrame(root, text="Κατάσταση / diagnostic log", padding=8)
        log_frame.grid(row=7, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="word", state="disabled", font=("Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(root)
        bottom.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        bottom.columnconfigure(1, weight=1)
        self.run_button = ttk.Button(bottom, text="Μετατροπή", command=self._start)
        self.run_button.grid(row=0, column=0, sticky="w")
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=1, padx=14, sticky="w")
        self.open_button = ttk.Button(bottom, text="Άνοιγμα αποτελεσμάτων", command=self._open_output, state="disabled")
        self.open_button.grid(row=0, column=2, sticky="e")

        ttk.Label(
            root,
            text="Το APP KEY χρησιμοποιείται μόνο στη μνήμη της εφαρμογής και δεν αποθηκεύεται από το probe.",
            foreground="#555555",
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(10, 0))

    @staticmethod
    def _default_output_for_pdf(pdf: Path) -> Path:
        return pdf.parent / f"{pdf.stem}_mathpix"

    def _choose_pdf(self) -> None:
        filename = filedialog.askopenfilename(
            title="Επιλογή PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not filename:
            return
        pdf = Path(filename)
        self.pdf_var.set(str(pdf))
        if not self.output_is_custom:
            self.output_var.set(str(self._default_output_for_pdf(pdf)))

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title="Επιλογή τελικού φακέλου αποτελεσμάτων")
        if folder:
            self.output_is_custom = True
            self.output_var.set(folder)

    def _mark_custom_output(self, _event=None) -> None:
        self.output_is_custom = bool(self.output_var.get().strip())

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key_var.get() else "•")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _start(self) -> None:
        source = Path(self.pdf_var.get().strip()).expanduser()
        key = self.key_var.get().strip()
        output_text = self.output_var.get().strip()

        if not source.is_file() or source.suffix.lower() != ".pdf":
            messagebox.showerror("Mathpix Lines Probe", "Επίλεξε έγκυρο αρχείο PDF.")
            return
        if not key:
            messagebox.showerror("Mathpix Lines Probe", "Βάλε το Mathpix APP KEY.")
            return

        run_dir = Path(output_text).expanduser() if output_text else self._default_output_for_pdf(source)
        self.output_var.set(str(run_dir))

        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.status_var.set("Μετατροπή…")
        self.last_output_dir = None
        self._append_log(f"PDF: {source}")
        self._append_log(f"ΑΠΟΤΕΛΕΣΜΑΤΑ: {run_dir}")
        self._append_log("Endpoint: " + ("EU" if self.eu_var.get() else "Global"))
        self._append_log("Υποβολή στο Mathpix…")

        threading.Thread(
            target=self._worker,
            args=(source.resolve(), key, run_dir.resolve(), self.eu_var.get()),
            daemon=True,
        ).start()

    def _worker(self, source: Path, key: str, run_dir: Path, use_eu: bool) -> None:
        try:
            probe.API_BASE = "https://eu.api.mathpix.com" if use_eu else "https://api.mathpix.com"
            run_dir.mkdir(parents=True, exist_ok=True)

            file_id = probe.submit_file(key, source)
            self.events.put(("log", f"file_id: {file_id}"))
            status = self._poll_with_gui(key, file_id)
            probe.write_json(run_dir / "status.json", status)
            if status.get("status") != "completed":
                raise probe.MathpixProbeError("Η Mathpix τερμάτισε με error. Δες το status.json.")

            self.events.put(("log", "Λήψη result.mmd…"))
            (run_dir / "result.mmd").write_bytes(probe.download_output(key, file_id, "mmd"))
            self.events.put(("log", "Λήψη result.lines.json…"))
            lines = probe.download_output(key, file_id, "lines.json")
            (run_dir / "result.lines.json").write_bytes(lines)

            summary = probe.inspect_lines_json(lines)
            probe.write_json(run_dir / "manifest.json", {
                "probe_version": 3,
                "execution_path": "Mathpix Files API /files/v1",
                "api_region": "eu" if use_eu else "global",
                "source": str(source),
                "output_directory": str(run_dir),
                "file_id": file_id,
                "outputs": {
                    "mmd": "result.mmd",
                    "lines_json": "result.lines.json",
                    "status": "status.json",
                },
                "lines_summary": summary,
            })

            self.events.put(("log", f"Σελίδες: {summary['page_count']}"))
            self.events.put(("log", f"Line objects: {summary['line_object_count']}"))
            if summary.get("line_types"):
                self.events.put(("log", "Types: " + json.dumps(summary["line_types"], ensure_ascii=False)))
            self.events.put(("done", run_dir))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_with_gui(self, key: str, file_id: str) -> dict:
        deadline = time.monotonic() + 900
        last = None
        while True:
            response = requests.get(
                f"{probe.API_BASE}/files/v1/{file_id}", headers={"app_key": key}, timeout=60
            )
            probe._raise_for_response(response, "Status poll")
            data = response.json()
            status = str(data.get("status", "unknown"))
            percent = data.get("percent_done")
            marker = (status, percent)
            if marker != last:
                suffix = f" — {percent}%" if percent is not None else ""
                self.events.put(("log", f"Κατάσταση: {status}{suffix}"))
                last = marker
            if status in probe.TERMINAL_STATES:
                return data
            if time.monotonic() >= deadline:
                raise probe.MathpixProbeError("Timeout 15 λεπτών.")
            time.sleep(2)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    self.last_output_dir = Path(payload)
                    self._append_log(f"ΟΛΟΚΛΗΡΩΘΗΚΕ: {self.last_output_dir}")
                    self.status_var.set("Ολοκληρώθηκε")
                    self.run_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    messagebox.showinfo("Mathpix Lines Probe", f"Η μετατροπή ολοκληρώθηκε.\n\n{self.last_output_dir}")
                elif kind == "error":
                    self._append_log("ERROR: " + str(payload))
                    self.status_var.set("Σφάλμα")
                    self.run_button.configure(state="normal")
                    messagebox.showerror("Mathpix Lines Probe", str(payload))
        except queue.Empty:
            pass
        self.after(120, self._drain_events)

    def _open_output(self) -> None:
        if self.last_output_dir and self.last_output_dir.exists():
            os.startfile(self.last_output_dir)  # type: ignore[attr-defined]


if __name__ == "__main__":
    MathpixProbeApp().mainloop()
