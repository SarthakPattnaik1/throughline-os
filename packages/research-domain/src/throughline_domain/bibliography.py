"""
The papers a project actually cited, as a bibliography (§73).

§73 asks for citation objects and then, in one line, for bibliography export.
The citation half was built and checked — entailment is *verified* per claim
rather than asserted by the author, which is more than the section asked. The
export half did not exist, and a researcher who cannot get their references out
of a tool has to retype them, which is where citations go wrong.

**Only what was cited, and only what is a paper.** A citation in this system
targets a passage, a source or an analysis run, because "the number came from
run 41" is as much a citation as "the claim is on page 6". An analysis run is
not a bibliography entry — there is no paper to put in one — so the export
walks from citations to the papers behind them and says how many it left out
rather than silently emitting fewer records than the project has citations.

**Nothing is invented.** A BibTeX record wants an author, a year and a journal,
and this corpus does not always have them: a PDF dropped on the workspace may
carry a title and nothing else. Fields that are missing are *omitted*, never
filled with "Anonymous" or "n.d." — a bibliography that guesses is worse than
one with a gap, because the gap is visible in the manuscript and the guess is
not. `missing_fields` reports what could not be written, per entry.

**The key is derived, not random.** A citation key is what a researcher types
into their manuscript, so it has to be stable across exports: the same paper
must produce the same key tomorrow. It is built from the first author's
surname, the year, and the first meaningful word of the title, which is the
convention every reference manager already follows.
"""

from __future__ import annotations

import re
from typing import Any

from throughline_schemas.words import counted

#: Fields a record needs before it reads as a complete reference.
WANTED = ("author", "year", "journal")

#: Characters BibTeX treats as syntax, escaped rather than dropped, so a title
#: containing "Smith & Jones" survives the round trip.
#:
#: Braces are here for a stronger reason than fidelity. They delimit a field,
#: so a title carrying a single unmatched "{" means the field never closes and
#: everything after it is swallowed — one malformed record from a scraped
#: source takes the whole bibliography with it rather than just its own entry.
#: Titles arrive from a parser as free text, so that is not a hypothetical.
#:
#: The cost is the "{DNA}" idiom, which protects a capital from a style that
#: would lowercase it. That is an authoring convention for .bib files written
#: by hand; this file is emitted from parsed metadata, where a brace is far
#: more likely to be noise than intent. A visible brace in one title is a
#: smaller harm than a bibliography that will not parse.
_ESCAPE = {
    # Backslash is the important one: it is the TeX command introducer.
    # Metadata is untrusted text, not author-supplied TeX, so preserving it
    # would let a scraped title containing an input/write command become an
    # instruction when the exported .bib is later compiled.
    "\\\\": r"\\textbackslash{}",
    "&": r"\\&",
    "%": r"\\%",
    "$": r"\\$",
    "#": r"\\#",
    "_": r"\\_",
    "{": r"\\{",
    "}": r"\\}",
    "^": r"\\textasciicircum{}",
    "~": r"\\textasciitilde{}",
}


def _escaped(text: str) -> str:
    return "".join(_ESCAPE.get(ch, ch) for ch in text)


def _surname(author: Any) -> str:
    """
    A surname from whatever shape the corpus recorded.

    Authors arrive as free text from a parser and there is no agreed shape:
    "Ada Lovelace", "Lovelace, Ada", or a dict from a structured source. Each
    is handled, and anything unrecognisable yields "" rather than a fragment of
    JSON in somebody's manuscript.
    """
    if isinstance(author, dict):
        for key in ("family", "surname", "last", "last_name"):
            if author.get(key):
                return str(author[key])
        author = author.get("name") or author.get("full_name") or ""
    text = str(author or "").strip()
    if not text:
        return ""
    if "," in text:                      # "Lovelace, Ada"
        return text.split(",", 1)[0].strip()
    return text.split()[-1]              # "Ada Lovelace"


def _year(publication_date: str | None) -> str:
    """The year, if the date carries one. Dates arrive as free text."""
    match = re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", publication_date or "")
    return match.group(1) if match else ""


def _key(surname: str, year: str, title: str, taken: set[str]) -> str:
    """
    A stable citation key, unique within one export.

    Two papers by the same author in the same year on similar subjects collide,
    and BibTeX silently keeps one. A suffix is appended rather than letting the
    second entry vanish — the researcher sees `lovelace2024a` and
    `lovelace2024b` and knows there are two.
    """
    word = ""
    for candidate in re.findall(r"[A-Za-z]{4,}", title):
        if candidate.lower() not in {"the", "and", "for", "with", "from",
                                     "into", "that", "this", "using"}:
            word = candidate.lower()
            break
    stem = "".join(part for part in (surname.lower(), year, word) if part)
    stem = re.sub(r"[^a-z0-9]", "", stem) or "citation"
    if stem not in taken:
        taken.add(stem)
        return stem
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        if f"{stem}{suffix}" not in taken:
            taken.add(f"{stem}{suffix}")
            return f"{stem}{suffix}"
    counter = 2
    while f"{stem}{counter}" in taken:
        counter += 1
    taken.add(f"{stem}{counter}")
    return f"{stem}{counter}"


def entries(cur, project_id: str) -> dict[str, Any]:
    """
    The papers this project's citations point at, ready to render.

    Ordered by key so two exports of an unchanged project are byte-identical —
    a bibliography that reshuffles itself makes a diff unreadable and a
    reviewer suspicious.
    """
    cur.execute(
        """
        SELECT DISTINCT p.id, p.title, p.authors, p.journal, p.publication_date,
                        p.doi, p.pmid, p.arxiv_id
          FROM citations c
          JOIN passages   pa ON pa.id = c.passage_id
          JOIN papers      p ON p.source_id = pa.source_id
         WHERE c.project_id = %s
        UNION
        SELECT DISTINCT p.id, p.title, p.authors, p.journal, p.publication_date,
                        p.doi, p.pmid, p.arxiv_id
          FROM citations c
          JOIN papers p ON p.source_id = c.source_id
         WHERE c.project_id = %s
        """,
        (project_id, project_id))
    papers = cur.fetchall()

    # Citations that name no paper at all — an analysis run, or a source with
    # nothing parsed behind it. Counted so the export can say so.
    cur.execute(
        "SELECT count(*) AS n FROM citations WHERE project_id = %s", (project_id,))
    total = cur.fetchone()["n"]

    taken: set[str] = set()
    made = []
    for row in sorted(papers, key=lambda r: (r["title"] or "", r["id"])):
        authors = row["authors"] if isinstance(row["authors"], list) else []
        surnames = [s for s in (_surname(a) for a in authors) if s]
        year = _year(row["publication_date"])
        made.append({
            "key": _key(surnames[0] if surnames else "", year,
                        row["title"] or "", taken),
            "title": row["title"] or "",
            "authors": surnames,
            "author_names": [str(a.get("name") if isinstance(a, dict) else a)
                             for a in authors],
            "journal": row["journal"] or "",
            "year": year,
            "doi": row["doi"] or "",
            "pmid": row["pmid"] or "",
            "arxiv_id": row["arxiv_id"] or "",
            "missing_fields": [
                field for field in WANTED
                if not {"author": surnames, "year": year,
                        "journal": row["journal"]}.get(field)],
        })
    return {"entries": made, "citations": total, "papers": len(made)}


def as_bibtex(cur, project_id: str) -> str:
    """
    A `.bib` file, or a comment saying why it is empty.

    An empty file is indistinguishable from a failed export once it is on
    somebody's disk, so a project that cited no papers says so in a BibTeX
    comment, which every reader and every tool ignores safely.
    """
    found = entries(cur, project_id)
    if not found["entries"]:
        return ("% No papers are cited in this project. "
                f"{counted(found['citations'], 'citation')} exist, and none "
                "of them points at a parsed paper.\n")

    out = []
    for entry in found["entries"]:
        fields = []
        if entry["author_names"]:
            fields.append(("author", " and ".join(
                _escaped(a) for a in entry["author_names"])))
        if entry["title"]:
            fields.append(("title", _escaped(entry["title"])))
        if entry["journal"]:
            fields.append(("journal", _escaped(entry["journal"])))
        if entry["year"]:
            fields.append(("year", entry["year"]))
        if entry["doi"]:
            fields.append(("doi", _escaped(entry["doi"])))
        if entry["arxiv_id"]:
            fields.append(("eprint", _escaped(entry["arxiv_id"])))
        if entry["pmid"]:
            fields.append(("note", f"PMID: {_escaped(entry['pmid'])}"))

        # `@misc` rather than `@article` where there is no journal: a record
        # typed as an article and missing its journal is one a bibliography
        # style will render with a dangling comma.
        kind = "article" if entry["journal"] else "misc"
        body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
        out.append(f"@{kind}{{{entry['key']},\n{body}\n}}")
    return "\n\n".join(out) + "\n"
