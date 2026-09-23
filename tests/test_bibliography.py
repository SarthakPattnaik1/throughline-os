"""
Getting the references back out (§73).

§73 asks for citation objects and then, in one line, for bibliography export.
The citation half was built and is better than the section asked — entailment
is checked per claim rather than asserted. The export half did not exist, and
was recorded as the gap in T121 before this closed it.

Two properties carry the file. **Nothing is invented**: a corpus assembled from
dropped PDFs often has a title and no author, and a bibliography that fills the
hole with "Anonymous, n.d." hides in a manuscript in a way an empty field does
not. And **the same project exports the same bytes**, because a reference key
is what a researcher types into their document — a key that changed between
exports would break every citation in the manuscript that used it.
"""

from __future__ import annotations

import json

import pytest
from throughline_domain import bibliography, citations
from throughline_domain.ids import new_id


def _paper(cur, project: str, *, title: str, authors: list, journal: str = "",
           date: str | None = None, doi: str = "", pmid: str = "",
           arxiv: str = "") -> str:
    """
    A source with a parsed paper behind it, and one passage to cite.

    These six columns are written here directly. For most of this file's life
    that was a row the product could not produce — `store_paper` wrote the
    title and the page count and left the citation fields behind, so every
    entry the export built was missing the author, year and journal of a paper
    whose author, year and journal a search had already stored one table over.
    The path that fills them is tested in
    `test_a_citation_keeps_what_the_search_found.py`; this file stays about
    what the export does with them once they are there.
    """
    source = new_id("src")
    cur.execute(
        "INSERT INTO sources(id, project_id, source_type, title, ingestion_status) "
        "VALUES (%s, %s, 'upload', %s, 'ready')", (source, project, title))
    cur.execute(
        "INSERT INTO papers(id, project_id, source_id, title, authors, journal, "
        "publication_date, doi, pmid, arxiv_id) "
        "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
        (new_id("pap"), project, source, title, json.dumps(authors), journal,
         date, doi or None, pmid or None, arxiv or None))
    return source


def _cite(cur, project: str, source: str) -> str:
    return citations.create_citation(cur, project_id=project, source_id=source,
                                     locator="p. 1", quoted_text="A sentence.")


def test_a_cited_paper_becomes_a_record(cur, project):
    source = _paper(cur, project, title="Attention and the Analyst",
                    authors=["Ada Lovelace", "Grace Hopper"],
                    journal="Journal of Method", date="2024-03-01",
                    doi="10.1000/xyz")
    _cite(cur, project, source)

    text = bibliography.as_bibtex(cur, project)

    assert "@article{" in text
    assert "Attention and the Analyst" in text
    assert "Lovelace" in text and "Hopper" in text
    assert "2024" in text
    assert "10.1000/xyz" in text


def test_a_paper_nobody_cited_is_not_in_the_bibliography(cur, project):
    """
    A bibliography is what was *cited*, not what was read.

    Exporting the whole corpus would put every paper a researcher skimmed into
    their reference list, which is the kind of padding a reviewer notices.
    """
    _paper(cur, project, title="Never Referred To", authors=["A Person"])
    cited = _paper(cur, project, title="Actually Used", authors=["B Person"])
    _cite(cur, project, cited)

    text = bibliography.as_bibtex(cur, project)

    assert "Actually Used" in text
    assert "Never Referred To" not in text


def test_missing_fields_are_left_out_rather_than_guessed(cur, project):
    """
    The property that matters most.

    A PDF dropped on the workspace often has a title and nothing else. "n.d."
    or "Anonymous" would render as a real reference in a manuscript, and the
    researcher would not see the invention until a reviewer did.
    """
    source = _paper(cur, project, title="An Untitled Preprint", authors=[])
    _cite(cur, project, source)

    found = bibliography.entries(cur, project)
    entry = found["entries"][0]

    assert entry["missing_fields"] == ["author", "year", "journal"]
    text = bibliography.as_bibtex(cur, project)
    assert "author" not in text
    assert "n.d." not in text and "Anonymous" not in text
    # And it is not typed as an article, which would render a dangling comma
    # where the journal should be.
    assert "@misc{" in text


def test_the_same_project_exports_the_same_bytes(cur, project):
    """A key is what a researcher types into a manuscript."""
    for i in range(3):
        source = _paper(cur, project, title=f"Paper Number {i}",
                        authors=[f"Author {i}"], date="2020")
        _cite(cur, project, source)

    assert bibliography.as_bibtex(cur, project) == bibliography.as_bibtex(cur, project)


def test_two_papers_that_would_share_a_key_both_survive(cur, project):
    """
    BibTeX keeps one of two entries with the same key, silently. The second
    reference would vanish from the manuscript with no error anywhere.
    """
    for _ in range(2):
        source = _paper(cur, project, title="Method Comparison Study",
                        authors=["Ada Lovelace"], journal="J", date="2024")
        _cite(cur, project, source)

    found = bibliography.entries(cur, project)
    keys = [e["key"] for e in found["entries"]]

    assert len(keys) == 2
    assert len(set(keys)) == 2, keys


def test_bibtex_syntax_in_a_title_is_escaped(cur, project):
    """`&` ends a field in BibTeX; a title carrying one would break the file."""
    source = _paper(cur, project, title="Smith & Jones: 50% of the Story",
                    authors=["A Smith"], journal="J", date="2024")
    _cite(cur, project, source)

    text = bibliography.as_bibtex(cur, project)

    assert r"\&" in text and r"\%" in text


def test_a_surname_is_found_whichever_way_the_name_was_recorded(cur, project):
    """Parsers disagree, and a fragment of JSON must not reach a manuscript."""
    source = _paper(cur, project, title="Shapes of Names",
                    authors=["Lovelace, Ada", {"family": "Hopper"},
                             {"name": "Alan Turing"}],
                    journal="J", date="2024")
    _cite(cur, project, source)

    entry = bibliography.entries(cur, project)["entries"][0]

    assert entry["authors"] == ["Lovelace", "Hopper", "Turing"]


def test_a_citation_that_names_no_paper_is_counted_not_dropped(cur, project):
    """
    A citation may point at an analysis run — "the number came from run 41" —
    which is a real citation and not a bibliography entry. The count says so
    rather than letting the file be quietly shorter than the project.
    """
    spec = new_id("aspec")
    cur.execute(
        "INSERT INTO analysis_specs(id, project_id, analysis_type, "
        "research_question, method, variables, content_hash, created_by) "
        "VALUES (%s, %s, 'correlation', 'q', 'pearson_correlation', "
        "'{}'::jsonb, %s, 'test')", (spec, project, "0" * 64))
    run = new_id("arun")
    cur.execute(
        "INSERT INTO analysis_runs(id, project_id, spec_id, status, result) "
        "VALUES (%s, %s, %s, 'completed', '{}'::jsonb)", (run, project, spec))
    citations.create_citation(cur, project_id=project, analysis_run_id=run,
                              locator="", quoted_text="")

    found = bibliography.entries(cur, project)

    assert found["citations"] == 1
    assert found["papers"] == 0


def test_an_empty_bibliography_says_so_rather_than_being_an_empty_file(cur, project):
    """An empty file on disk is indistinguishable from a failed export."""
    text = bibliography.as_bibtex(cur, project)

    assert text.startswith("%")
    assert "No papers are cited" in text


# ---------------------------------------------------------------------------
# The file has to survive the titles it is given
# ---------------------------------------------------------------------------


def _entries_close(text: str) -> bool:
    """Every *structural* brace in the file is matched.

    An escaped brace is a literal character, not a delimiter, so a checker that
    counted `\\{` would report the escaping as the corruption it prevents —
    which is what the first version of this did.
    """
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in "{}":
            index += 2          # an escaped brace is a character in the text
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        index += 1
    return depth == 0


class TestOneBadTitleCannotTakeTheFileWithIt:
    """
    Titles arrive from a parser as free text — `_surname` says so in as many
    words — so the escaping has to hold for text nobody wrote by hand.

    An unbalanced brace is the case that matters. BibTeX delimits a field with
    braces, so a title carrying a single "{" means the field never closes and
    everything after it is swallowed: one malformed record from a scraped
    source destroys the whole bibliography rather than its own entry. Every
    test here checked for a substring, and a substring is present in a corrupt
    file too.
    """

    def test_an_unclosed_brace_does_not_run_past_its_field(self, cur, project):
        source = _paper(cur, project, title="An open { brace",
                        authors=["Ada Lovelace"], journal="J", date="2024-01-01")
        _cite(cur, project, source)

        text = bibliography.as_bibtex(cur, project)
        assert _entries_close(text), (
            "one title with an unbalanced brace corrupts the whole file")

    def test_a_stray_closing_brace_does_not_end_the_entry_early(
            self, cur, project):
        source = _paper(cur, project, title="A close } brace",
                        authors=["Ada Lovelace"], journal="J", date="2024-01-01")
        _cite(cur, project, source)

        assert _entries_close(bibliography.as_bibtex(cur, project))

    def test_the_entry_after_a_bad_one_is_still_there(self, cur, project):
        """The consequence that makes this worth fixing rather than noting."""
        first = _paper(cur, project, title="An open { brace",
                       authors=["Ada Lovelace"], journal="J", date="2024-01-01")
        second = _paper(cur, project, title="Perfectly Ordinary Title",
                        authors=["Grace Hopper"], journal="J", date="2024-02-01")
        _cite(cur, project, first)
        _cite(cur, project, second)

        text = bibliography.as_bibtex(cur, project)
        assert _entries_close(text)
        assert "Perfectly Ordinary Title" in text
        assert text.count("@article{") == 2

    def test_the_ordinary_escapes_still_work(self, cur, project):
        """The five characters that were already handled, unchanged."""
        source = _paper(cur, project, title="50% of Smith & Jones_1 costs $2 #3",
                        authors=["Ada Lovelace"], journal="J", date="2024-01-01")
        _cite(cur, project, source)

        text = bibliography.as_bibtex(cur, project)
        assert _entries_close(text)
        for escaped in (r"\%", r"\&", r"\_", r"\$", r"\#"):
            assert escaped in text, escaped

    def test_tex_commands_from_metadata_are_rendered_as_literal_text(
            self, cur, project):
        source = _paper(
            cur,
            project,
            title=r"Results \input{/etc/passwd} ^ ~",
            authors=[r"Eve \write18{touch /tmp/owned}"],
            journal="J",
            date="2024-01-01",
        )
        _cite(cur, project, source)

        text = bibliography.as_bibtex(cur, project)

        assert r"\input" not in text
        assert r"\write18" not in text
        assert r"\textbackslash{}input" in text
        assert r"\textbackslash{}write18" in text
        assert r"\textasciicircum{}" in text
        assert r"\textasciitilde{}" in text
        assert _entries_close(text)

    def test_a_plain_title_is_untouched(self, cur, project):
        source = _paper(cur, project, title="Attention and the Analyst",
                        authors=["Ada Lovelace"], journal="J", date="2024-01-01")
        _cite(cur, project, source)

        text = bibliography.as_bibtex(cur, project)
        assert "Attention and the Analyst" in text
