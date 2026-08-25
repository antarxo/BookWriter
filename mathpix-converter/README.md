# PDF/Mathpix to DOCX Converter

Standalone UI for reconstructing a high-fidelity Word document from:

- the original PDF,
- the Mathpix Markdown/image ZIP,
- the Mathpix DOCX donor.

The converter product is a human-editable reconstructed DOCX plus fidelity
reports. BookWriter canonicalization remains the Author's responsibility.

Current local endpoint:

```text
POST /api/convert-mathpix-docx
  pdf: original PDF
  markdown: Mathpix Markdown/images ZIP
  docx: Mathpix DOCX donor
  pages: explicit PDF page range
  renderFidelity: 1/0
  calibration: fast/full/none
```

The endpoint builds the internal working package itself. The user should not
need to prepare a combined ZIP.

Local launcher:

```text
Start_Mathpix_Converter.cmd
```

The launcher starts the BookWriter local gateway and opens this standalone
converter UI directly.
