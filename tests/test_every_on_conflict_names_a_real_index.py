"""
Every ON CONFLICT target has to name a unique index that still exists.

`render_visual`'s web-spec path upserted `ON CONFLICT (visual_id, format,
spec_hash)`. Migration 0030 dropped that constraint and replaced it with an
index on `COALESCE(height_px, -1)`, the file path was moved to match, and this
one was not — so every vega-lite render from then on failed with "there is no
unique or exclusion constraint matching the ON CONFLICT specification". Nothing
in the interface asks for vega-lite, so no test and no person ever saw it.

Postgres checks a conflict target only when the statement runs, and a
statement on a path nothing exercises never runs. So this reads the migrations
for the unique indexes that exist at the end — applying every CREATE, DROP and
ALTER in the order it is written, since a drop followed by a rebuild is the
ordinary case — and every ON CONFLICT in the product, and requires each target
to match one.

Validated against history before it was written down: run against the revision
before the fix it reports exactly the vega-lite target and nothing else.
"""

from __future__ import annotations

import pathlib
import re

from test_sql_references import source_files, string_literals

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / (
    "packages/research-domain/src/throughline_domain/migrations")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _split(text: str) -> list[str]:
    out, depth, current = [], 0, ""
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        out.append(current)
    return [part for part in out if part.strip()]


def _paren(text: str, at: int) -> tuple[str, int]:
    depth = 0
    for index in range(at, len(text)):
        depth += text[index] == "("
        depth -= text[index] == ")"
        if depth == 0:
            return text[at + 1:index], index
    raise ValueError("unbalanced parentheses")


def unique_indexes() -> dict[str, dict[str, frozenset[str]]]:
    """Table → {index or constraint name → the columns it is unique on}."""
    unique: dict[str, dict[str, frozenset[str]]] = {}

    def apply(statement: str) -> None:
        s = statement.strip()
        m = re.match(r"(?is)CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\(", s)
        if m:
            table = m.group(1).lower()
            body, _ = _paren(s, m.end() - 1)
            found = unique.setdefault(table, {})
            for part in _split(body):
                p = part.strip()
                key = re.match(r"(?is)(?:CONSTRAINT\s+(\w+)\s+)?(PRIMARY KEY|UNIQUE)"
                               r"\s*\((.*)\)\s*$", p)
                if key:
                    cols = [_norm(c) for c in _split(key.group(3))]
                    name = key.group(1) or (
                        f"{table}_pkey" if key.group(2).upper() == "PRIMARY KEY"
                        else f"{table}_{'_'.join(cols)}_key")
                    found[name.lower()] = frozenset(cols)
                    continue
                inline = re.match(r"(?is)(\w+)\s+\w.*?\b(PRIMARY KEY|UNIQUE)\b", p)
                if inline and not re.match(r"(?i)(CHECK|FOREIGN|CONSTRAINT|EXCLUDE)\b", p):
                    col = inline.group(1).lower()
                    found[f"{table}_pkey" if inline.group(2).upper() == "PRIMARY KEY"
                          else f"{table}_{col}_key"] = frozenset([col])
            return
        m = re.match(r"(?is)CREATE UNIQUE INDEX (?:IF NOT EXISTS )?(\w+)\s+ON\s+(\w+)"
                     r"\s*(?:USING\s+\w+\s*)?\(", s)
        if m:
            body, end = _paren(s, m.end() - 1)
            cols = frozenset(_norm(c) for c in _split(body))
            if re.match(r"(?is)\s*WHERE\b", s[end + 1:]):
                cols |= {"__partial__"}
            found = unique.setdefault(m.group(2).lower(), {})
            name = m.group(1).lower()
            if not ("IF NOT EXISTS" in s.upper() and name in found):
                found[name] = cols
            return
        m = re.match(r"(?is)DROP INDEX (?:IF EXISTS )?(\w+)", s)
        if m:
            for found in unique.values():
                found.pop(m.group(1).lower(), None)
            return
        m = re.match(r"(?is)ALTER TABLE\s+(\w+)(.*)", s)
        if m:
            table = m.group(1).lower()
            found = unique.setdefault(table, {})
            for clause in _split(m.group(2)):
                c = clause.strip()
                drop = re.match(r"(?is)DROP CONSTRAINT (?:IF EXISTS )?(\w+)", c)
                if drop:
                    found.pop(drop.group(1).lower(), None)
                    continue
                add = re.match(r"(?is)ADD (?:CONSTRAINT (\w+) )?UNIQUE\s*\((.*)\)", c)
                if add:
                    cols = [_norm(x) for x in _split(add.group(2))]
                    found[(add.group(1) or f"{table}_{'_'.join(cols)}_key").lower()] = (
                        frozenset(cols))
                    continue
                col = re.match(r"(?is)ADD COLUMN (?:IF NOT EXISTS )?(\w+)\s.*\bUNIQUE\b", c)
                if col:
                    found[f"{table}_{col.group(1).lower()}_key"] = frozenset(
                        [col.group(1).lower()])

    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text())
        # Function bodies carry semicolons of their own.
        sql = re.sub(r"(?s)\$\$.*?\$\$", "''", sql)
        for statement in sql.split(";"):
            apply(statement)
    return unique


def matches(table: str, target: frozenset[str],
            unique: dict[str, dict[str, frozenset[str]]]) -> bool:
    return any(target == cols - {"__partial__"}
               for cols in unique.get(table, {}).values())


def conflict_targets() -> list[tuple[pathlib.Path, str, str, frozenset[str] | None]]:
    """(file, table, as written, columns) for every ON CONFLICT in the product."""
    found = []
    for path in source_files():
        for literal in string_literals(path):
            query = re.sub(r"--[^\n]*", "", literal)
            for m in re.finditer(r"(?is)INSERT\s+INTO\s+(\w+).*?ON\s+CONFLICT\s*"
                                 r"(\(|ON\s+CONSTRAINT\s+(\w+)|DO\b)", query):
                table = m.group(1).lower()
                if m.group(3):
                    found.append((path, table, f"ON CONSTRAINT {m.group(3)}", None))
                elif m.group(2) == "(":
                    body, _ = _paren(query, m.start(2))
                    found.append((path, table, f"({' '.join(body.split())})",
                                  frozenset(_norm(c) for c in _split(body))))
    return found


def test_every_on_conflict_target_names_a_unique_index_that_exists():
    unique = unique_indexes()
    wrong = [(path, table, written) for path, table, written, cols in conflict_targets()
             if (cols is not None and not matches(table, cols, unique))
             or (cols is None and written.split()[-1].lower() not in unique.get(table, {}))]
    assert not wrong, (
        "These ON CONFLICT targets match no unique index or constraint that "
        "still exists, so every call that reaches them fails:\n"
        + "\n".join(f"  {p.name}: {t} {w}" for p, t, w in wrong))


def test_the_scan_reads_enough_to_mean_something():
    """
    A check whose whole value is finding nothing has to earn the nothing.

    Nineteen clauses name a target when this was written — twenty-three in
    all, the other four being `ON CONFLICT DO NOTHING`, which has none to
    check. The floor sits below that with room for code to move, and far
    enough above zero that a parser which stopped finding them would fail.
    """
    assert sum(len(v) for v in unique_indexes().values()) > 80
    assert len(conflict_targets()) >= 15


def test_it_notices_the_target_that_actually_broke():
    """
    The vega-lite target as it was before the fix, against today's schema. If
    this ever matches, the check has stopped being able to see the bug it was
    written for.
    """
    unique = unique_indexes()
    before = frozenset(["visual_id", "format", "spec_hash"])
    assert not matches("visual_renders", before, unique)
    # Widened twice since: by the renderer (0045) and by the ground (0047).
    # Each widening makes the narrower target a broken one, which is exactly
    # what this check exists to see.
    without_ground = frozenset(["visual_id", "format", "spec_hash",
                                "coalesce(height_px,-1)", "renderer"])
    assert not matches("visual_renders", without_ground, unique)
    after = without_ground | {"ground", "transparent"}
    assert matches("visual_renders", after, unique)


def test_a_drop_followed_by_a_rebuild_is_read_in_order():
    """
    The first version of this read every CREATE before every DROP, so a
    migration that dropped an index and rebuilt it wider was read as having
    removed it — and three correct targets were reported as broken.
    """
    names = unique_indexes()["visual_renders"]
    assert "idx_visual_renders_identity" in names
    assert "renderer" in names["idx_visual_renders_identity"]
