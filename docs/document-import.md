# Document Import

PAL can ingest local documents into the vault as grounded wiki articles.

## Supported Formats

PDF, DOCX, XLSX, PPTX, HTML, EPUB, CSV, and plain markdown.

## Workflow

1. Place the file in `raw/` in your vault (any subdirectory under `raw/` works).
2. Run `/import raw/filename.pdf` (or whatever the file type).
3. PAL:
   - Converts the document to markdown (via `markitdown` + `pymupdf4llm` for PDFs)
   - For PDFs: detects chapter boundaries using a three-tier fallback (embedded TOC → typography → LLM-based TOC detection)
   - Summarizes each chapter or section into `raw/summaries/`
   - Compiles each summary into the vault's article format via `compile_batch`
   - Categorizes each article into the best-fitting vault directory based on content
   - Archives the source file to `raw/archived/` with a 30-day cleanup

## Chapter Detection (PDFs)

For structured PDFs, the detector uses three tiers in order:

1. **Embedded TOC** (tier 1) — pull chapter boundaries from the PDF's own outline if present. Fastest; no inference.
2. **Typography-based detection** (tier 2) — analyze font sizes and styles to infer heading structure. No inference.
3. **LLM-based TOC detection** (tier 3) — only fires when tiers 1 and 2 fail. Extracts a compact sample from the PDF and asks an LLM to identify chapter boundaries. With `PAL_BATCH_ENABLED=true` this call routes to the batch slot; otherwise it uses the main model.

Tier 3 is the rare case — most well-structured PDFs never need it.

## Output

Each imported chapter becomes its own article under the appropriate vault directory, following the standard article format (see [article-format.md](article-format.md)). The source PDF's path goes into each article's source metadata for provenance.

## Limitations

- **No OCR pass**: scanned PDFs without embedded text produce empty summaries. Run OCR externally first.
- **Table-heavy documents**: tables convert to markdown but complex layouts may lose structure.
- **Very large documents**: the pipeline handles book-length PDFs, but expect minutes-per-chapter throughput depending on the model.
