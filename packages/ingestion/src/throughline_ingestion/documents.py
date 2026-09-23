"""Document parsing.

Pure functions: bytes on disk in, structured passages out. No database, no
network, no model calls — which is what makes parsing independently testable and
keeps research logic out of route handlers.

Every passage carries the location it came from.  requires that every
retrieved sentence remains traceable to the paper, so `char_start`/`char_end`
index into the document's reconstructed text and are verified on read.
"""

from __future__ import annotations

import logging

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pymupdf


class UnsupportedFormat(ValueError):
    """ — do not show unsupported formats as functional."""


#: Formats the pipeline genuinely handles today. Anything else is refused loudly
#: rather than silently producing an empty document.
SUPPORTED_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".markdown"})
SUPPORTED_DATASET_SUFFIXES = frozenset({".csv", ".tsv", ".xlsx", ".xlsm", ".json"})


_log = logging.getLogger("throughline.ingestion")


@dataclass(slots=True)
class Passage:
    ordinal: int
    content: str
    locator: str
    kind: str = "body"
    page: int | None = None
    section: str = ""
    paragraph_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedDocument:
    text: str
    passages: list[Passage]
    title: str = ""
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def verify_anchors(self) -> list[int]:
        """Return the ordinals of any passage whose offsets do not match `text`.

        This rule depends on offsets being real. This is cheap and runs after every
        parse so a bad anchor is caught at ingestion rather than discovered later
        by a researcher inspecting a citation.
        """
        broken: list[int] = []
        for passage in self.passages:
            if passage.char_start is None or passage.char_end is None:
                broken.append(passage.ordinal)
                continue
            if self.text[passage.char_start : passage.char_end] != passage.content:
                broken.append(passage.ordinal)
        return broken


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").replace("\x00", " ")
    text = re.sub(r"[\t\r]+", " ", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("abstract", re.compile(r"^\s*abstract\b", re.I)),
    ("introduction", re.compile(r"^\s*(?:1\.?\s*)?(introduction|background)\b", re.I)),
    ("methods", re.compile(r"^\s*(?:\d\.?\s*)?(methods?|materials?\s+and\s+methods?|methodology|study\s+design)\b", re.I)),
    ("results", re.compile(r"^\s*(?:\d\.?\s*)?(results?|findings?)\b", re.I)),
    ("discussion", re.compile(r"^\s*(?:\d\.?\s*)?(discussion|interpretation)\b", re.I)),
    ("limitations", re.compile(r"^\s*(?:\d\.?\s*)?(limitations?|strengths?\s+and\s+limitations?)\b", re.I)),
    ("conclusion", re.compile(r"^\s*(?:\d\.?\s*)?(conclusions?|summary)\b", re.I)),
    ("funding", re.compile(r"^\s*(funding|financial\s+support|acknowledg(?:e)?ments?)\b", re.I)),
    ("conflicts", re.compile(r"^\s*(conflicts?\s+of\s+interest|competing\s+interests?)\b", re.I)),
    ("references", re.compile(r"^\s*(references|bibliography)\b", re.I)),
]


def detect_section(block_text: str, current: str) -> str:
    """A heading changes the section; body text inherits the one in force."""
    head = block_text.strip()[:120]
    for label, pattern in SECTION_PATTERNS:
        if pattern.match(head):
            return label
    return current


def _page_blocks_in_reading_order(
    page: pymupdf.Page, *, spliced: list[int] | None = None,
) -> list[str]:
    """Order text blocks column by column.

    PyMuPDF's ``sort=True`` orders blocks top-to-bottom across the whole page
    width. On a two-column academic PDF that interleaves the columns and splices
    unrelated sentences together, which destroys the verbatim text every citation
    depends on. Detect the column split and read each column fully first.

    When the block extraction itself fails there is nothing to detect a column
    split *with*, so the fallback is the whole-page ``sort=True`` read — which
    is precisely the interleaving the paragraph above exists to avoid. That is
    still better than losing the page, and it must not be silent: a spliced
    sentence is indistinguishable from a real one, and it would be quoted as
    verbatim. The page number is appended to ``spliced`` so the caller can
    record which pages this happened on, and the failure is logged the way the
    table extraction below already logs its own.
    """
    try:
        blocks = [b for b in page.get_text("blocks") if len(b) >= 5 and str(b[4] or "").strip()]
    except Exception as exc:  # noqa: BLE001 — reported, never silent; see above
        _log.warning(
            "block extraction failed on page %s: %s: %s — falling back to a "
            "whole-page read, which can splice columns together",
            page.number, type(exc).__name__, exc)
        if spliced is not None:
            spliced.append(int(page.number) + 1)
        return [normalize_text(page.get_text("text", sort=True))]
    if not blocks:
        return []

    rect = page.rect
    mid = (rect.x0 + rect.x1) / 2
    gutter = rect.width * 0.06
    left = [b for b in blocks if (b[0] + b[2]) / 2 < mid]
    right = [b for b in blocks if (b[0] + b[2]) / 2 >= mid]
    # Full-width titles, tables and figures straddle the gutter; their presence
    # means the page is not cleanly two-column.
    straddles = any(b[0] < mid - gutter and b[2] > mid + gutter for b in blocks)

    if len(left) >= 2 and len(right) >= 2 and not straddles:
        ordered = sorted(left, key=lambda b: (round(b[1], 1), b[0])) + sorted(
            right, key=lambda b: (round(b[1], 1), b[0])
        )
    else:
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
    return [normalize_text(str(b[4])) for b in ordered if normalize_text(str(b[4]))]


def parse_pdf(path: Path) -> ParsedDocument:
    passages: list[Passage] = []
    # Pages whose columns could not be told apart; see the note in
    # `_page_blocks_in_reading_order`. Empty on every ordinary paper.
    spliced: list[int] = []
    chunks: list[str] = []
    cursor = 0
    ordinal = 0
    section = ""
    page_count = 0
    doc_title = ""

    with pymupdf.open(path) as doc:
        page_count = doc.page_count
        doc_title = normalize_text(str(doc.metadata.get("title") or ""))
        for page_index, page in enumerate(doc, start=1):
            for paragraph_index, block in enumerate(
                    _page_blocks_in_reading_order(page, spliced=spliced), start=1):
                section = detect_section(block, section)
                start = cursor
                end = start + len(block)
                passages.append(
                    Passage(
                        ordinal=ordinal,
                        content=block,
                        locator=f"p. {page_index} ¶{paragraph_index}",
                        page=page_index,
                        section=section,
                        paragraph_index=paragraph_index,
                        char_start=start,
                        char_end=end,
                        kind="references" if section == "references" else "body",
                    )
                )
                chunks.append(block)
                cursor = end + 2  # the "\n\n" joiner below
                ordinal += 1

            # Tables are extracted separately so a number keeps its structure.
            try:
                finder = page.find_tables()
                for table_index, table in enumerate(getattr(finder, "tables", []) or [], start=1):
                    rows = [
                        " | ".join(normalize_text(str(cell or "")) for cell in row)
                        for row in table.extract()
                    ]
                    table_text = normalize_text("\n".join(r for r in rows if r.strip(" |")))
                    if not table_text:
                        continue
                    start = cursor
                    end = start + len(table_text)
                    passages.append(
                        Passage(
                            ordinal=ordinal,
                            content=table_text,
                            locator=f"p. {page_index} table {table_index}",
                            page=page_index,
                            section=section,
                            kind="table",
                            char_start=start,
                            char_end=end,
                            metadata={"table_index": table_index, "extractor": "pymupdf"},
                        )
                    )
                    chunks.append(table_text)
                    cursor = end + 2
                    ordinal += 1
            except Exception as exc:  # noqa: BLE001 — prose must survive this
                # A table-detection failure must not lose the page's prose, so
                # it is caught. It was also silent, which is a different thing:
                # an extractor broken on every page of every document looked
                # exactly like a corpus that happens to contain no tables.
                #
                # That matters here more than it would elsewhere. This system
                # tests claims against tables and quotes them verbatim, so a
                # table that never arrives is not a cosmetic loss — it is
                # evidence the researcher will never know was available.
                _log.warning("table extraction failed on page %s of %s: %s: %s",
                             page_index, path.name, type(exc).__name__, exc)

    text = "\n\n".join(chunks)
    if not doc_title and passages:
        doc_title = passages[0].content.split("\n", 1)[0][:300]
    return ParsedDocument(
        text=text,
        passages=passages,
        title=doc_title,
        page_count=page_count,
        # `"columns": "detected"` was stated unconditionally, including for the
        # pages where detection is exactly what failed. It now says which
        # happened, and names the pages, because "some of this paper's text may
        # be spliced" is unusable without knowing where.
        metadata={
            "parser": "pymupdf",
            "columns": "spliced" if spliced else "detected",
            **({"spliced_pages": spliced} if spliced else {}),
        },
    )


def parse_docx(path: Path) -> ParsedDocument:
    from docx import Document

    from .archive_safety import UnsafeArchive, check_zip_container

    try:
        check_zip_container(path)
    except UnsafeArchive as exc:
        raise UnsupportedFormat(
            f"This .docx is unsafe to expand in the ingestion worker ({exc})"
        ) from exc

    document = Document(path)
    passages: list[Passage] = []
    chunks: list[str] = []
    cursor = 0
    section = ""
    for index, paragraph in enumerate(document.paragraphs, start=1):
        content = normalize_text(paragraph.text)
        if not content:
            continue
        style = (paragraph.style.name if paragraph.style else "") or ""
        if style.lower().startswith("heading"):
            section = detect_section(content, content[:120])
        start = cursor
        end = start + len(content)
        passages.append(
            Passage(
                ordinal=len(passages),
                content=content,
                locator=f"¶{index}",
                section=section,
                paragraph_index=index,
                char_start=start,
                char_end=end,
                kind="heading" if style.lower().startswith("heading") else "body",
            )
        )
        chunks.append(content)
        cursor = end + 2
    text = "\n\n".join(chunks)
    title = passages[0].content[:300] if passages else path.stem
    return ParsedDocument(text=text, passages=passages, title=title,
                          metadata={"parser": "python-docx"})


def parse_plain_text(path: Path) -> ParsedDocument:
    raw = normalize_text(path.read_text(encoding="utf-8", errors="replace"))
    passages: list[Passage] = []
    chunks: list[str] = []
    cursor = 0
    section = ""
    for index, block in enumerate(
        [b.strip() for b in raw.split("\n\n") if b.strip()], start=1
    ):
        section = detect_section(block, section)
        start = cursor
        end = start + len(block)
        passages.append(
            Passage(
                ordinal=len(passages),
                content=block,
                locator=f"¶{index}",
                section=section,
                paragraph_index=index,
                char_start=start,
                char_end=end,
            )
        )
        chunks.append(block)
        cursor = end + 2
    return ParsedDocument(text="\n\n".join(chunks), passages=passages,
                          title=path.stem, metadata={"parser": "plain-text"})


def _parse_pdf_with_the_best_available_parser(path: Path) -> ParsedDocument:
    """Docling when it is installed and it works; PyMuPDF otherwise.

    `structured.py` was written for exactly this fork and then nothing called
    it, so on a machine with Docling installed every paper was still read as
    flat text blocks — and the module's own docstring said the interface
    reports which parser ran, which nothing could do while the answer was
    always the same one.

    Three things make this a fallback rather than a switch:

    - **An optional parser must never fail an ingestion.** Docling loads
      models and can raise for reasons that have nothing to do with the
      document. Any exception hands the file to PyMuPDF, which was going to
      read it before this function existed.

    - **The anchors are checked before the result is accepted.** A passage's
      character offsets are what a quotation in a report resolves against, so
      a structured parse whose offsets do not match its own text is worse than
      the flat one — it is wrong in the place the product makes promises
      about. `verify_anchors` already answers this; here it decides.

    - **Whichever ran is recorded on the document.** Two researchers on
      differently-configured machines can get different passages out of the
      same paper, and that difference has to be attributable rather than
      invisible.
    """
    # Imported here rather than at module scope: `structured` imports
    # `ParsedDocument` from this module, so a top-level import is a cycle.
    from . import structured

    # Why Docling did not read it, when it was there to. Recording only the parser
    # that ran made "Docling is not installed" and "Docling crashed" read the
    # same — against the promise above that which parser ran is attributable.
    # Found when Docling ran out of GPU memory on an 8 GB Mac mid-suite and the
    # paper was quietly read flat (T168).
    fell_back_because: str | None = None
    if structured.available():
        try:
            parsed = structured.parse(path)
        except Exception as exc:  # noqa: BLE001 - any failure falls back; see above
            parsed = None
            fell_back_because = (
                f"Docling could not read it ({type(exc).__name__}: "
                f"{str(exc)[:200]})")
        if parsed is not None:
            broken = parsed.verify_anchors()
            if not broken:
                parsed.metadata = {**parsed.metadata, "parser": "docling"}
                return parsed
            fell_back_because = (
                f"Docling's reading had {len(broken)} passage anchors that did not "
                "match its own text, so the flat reading was used instead")

    flat = parse_pdf(path)
    flat.metadata = {**flat.metadata, "parser": "pymupdf"}
    if fell_back_because:
        flat.metadata["parser_fallback"] = fell_back_because
    return flat


def parse_document(path: Path, *, suffix: str | None = None) -> ParsedDocument:
    """Parse a document.

    ``suffix`` is passed explicitly because files are stored content-addressed:
    the path on disk is a hash with no extension, so the format must come from
    the original filename recorded at upload.
    """
    suffix = (suffix or path.suffix).lower()
    if suffix == ".pdf":
        return _parse_pdf_with_the_best_available_parser(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix in {".txt", ".md", ".markdown"}:
        return parse_plain_text(path)
    raise UnsupportedFormat(
        f"{suffix or 'this file type'} is not supported for document parsing. "
        f"Supported: {', '.join(sorted(SUPPORTED_DOCUMENT_SUFFIXES))}"
    )
