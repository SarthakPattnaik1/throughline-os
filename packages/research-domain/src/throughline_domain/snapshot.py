"""
A project, as one file somebody can archive or carry to another machine (§75).

This system is local-first: the work lives on one researcher's disk, and the
failure that matters is losing it or being unable to move it. A snapshot is the
answer to "give me everything about this project", and until now the answer was
to copy the whole installation with `backup.sh` — every project, every other
researcher's work included.

**The tables are discovered, not listed.** Forty-three of them carry a
`project_id` today, and a hand-written list would omit the forty-fourth
silently — the row would simply not be in the archive, and nobody would know
until they needed it. `information_schema` is asked instead, so a table added
next month is included without anybody remembering to add it.

**What is left out is named, with a reason.** An omission a reader can see is a
decision; one they cannot is a defect waiting to be discovered by somebody
restoring an incomplete archive.

**It is an archive, not yet a restore.** Nothing here reads a snapshot back in.
Saying so is the whole of the honesty available: a file called a backup that
cannot be restored is worse than no file, because it is trusted.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

#: Tables deliberately left out, and why. Each entry is a claim somebody can
#: argue with rather than an absence they have to notice.
EXCLUDED: dict[str, str] = {
    "passage_embeddings":
        "Vectors are large and derivable: re-embedding the passages produces "
        "them again, and carrying them would multiply the file size for "
        "something the machine can rebuild.",
    "retrieval_events":
        "A log of searches somebody ran. It describes the researcher's "
        "behaviour rather than their research, and a snapshot handed to a "
        "collaborator should not carry it.",
    "retrieval_results":
        "What each of those searches returned — the same log, one level down, "
        "and left out for the same reason.",
}

#: Tables that belong to the installation rather than to any project, and so
#: are in no project's snapshot. Named, like `EXCLUDED`, so the completeness
#: check can tell a decision from an oversight.
INSTALLATION: dict[str, str] = {
    "projects": "The project's own row travels as `project`, not as a table.",
    "users": "Accounts on this machine, not the research.",
    "sessions": "Who is signed in right now; a secret, and nothing to carry.",
    "installation_settings": "This machine's settings, shared by every project.",
    "installation_secrets": "Keys for this machine. Never exported.",
    "setting_history": "The history of this machine's settings.",
    "schema_migrations": "Which migrations this database has run.",
}

#: The version of the snapshot's shape. A reader that finds a number it does
#: not know should say so rather than guess at the contents.
FORMAT_VERSION = 1


def project_tables(cur) -> list[str]:
    """Every table carrying a project_id, minus the ones excluded by name."""
    cur.execute(
        """
        SELECT DISTINCT table_name FROM information_schema.columns
         WHERE column_name = 'project_id' AND table_schema = 'public'
         ORDER BY table_name
        """
    )
    found = [row["table_name"] for row in cur.fetchall()]
    return [name for name in found if name not in EXCLUDED]


def child_tables(cur) -> dict[str, str]:
    """
    Tables with no `project_id` of their own that belong to a project through a
    parent, each with the SQL that selects one project's rows.

    Discovering only `project_id` columns left out every table that reaches the
    project through another: a dataset's versions and columns, the text of a
    report (`artifact_blocks`) and its citations, validation and assumption
    checks, a finding's claims and lifecycle — fifteen tables, silently absent
    from an archive that said it held "everything about this project" (T171).
    The foreign keys say which parent each belongs to, so they are followed
    rather than listed: a table added next month with a key to a project's row
    is included the same way. A table reachable only through an excluded one is
    not resolved here, and has to be named in `EXCLUDED` like its parent.
    """
    cur.execute(
        """
        SELECT kcu.table_name AS child, kcu.column_name AS child_column,
               ccu.table_name AS parent, ccu.column_name AS parent_column
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_name = tc.constraint_name
           AND kcu.table_schema = tc.table_schema
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
           AND ccu.table_schema = tc.table_schema
         WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
         ORDER BY kcu.table_name, ccu.table_name, kcu.column_name
        """
    )
    keys = [dict(row) for row in cur.fetchall()]
    direct = set(project_tables(cur))
    # Where each resolved table's rows for one project come from.
    where: dict[str, str] = {name: "project_id = %s" for name in direct}
    resolved: dict[str, str] = {}
    unresolved = {k["child"] for k in keys} - direct - set(EXCLUDED) - set(INSTALLATION)
    changed = True
    while changed:
        changed = False
        for child in sorted(unresolved - set(resolved)):
            for key in keys:
                if key["child"] != child or key["parent"] not in where:
                    continue
                condition = (
                    f"{key['child_column']} IN (SELECT {key['parent_column']} "
                    f"FROM {key['parent']} WHERE {where[key['parent']]})")
                where[child] = condition
                resolved[child] = condition
                changed = True
                break
    return resolved


def coverage(cur) -> dict[str, str]:
    """
    Every table in the schema, and why it is or is not in a snapshot.

    What the completeness test reads: a table that is none of exported directly,
    exported through its parent, excluded with a reason, or the installation's
    is one nobody decided about.
    """
    # The other two first: they use this cursor too, and reading the table list
    # after them read *their* last result instead — which is how this check
    # first passed without looking at the real list.
    direct = set(project_tables(cur))
    through = child_tables(cur)
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")
    names = [row["table_name"] for row in cur.fetchall()]
    answer: dict[str, str] = {}
    for name in names:
        if name in direct:
            answer[name] = "exported"
        elif name in through:
            answer[name] = "exported through its parent"
        elif name in EXCLUDED:
            answer[name] = f"excluded: {EXCLUDED[name]}"
        elif name in INSTALLATION:
            answer[name] = f"not a project's: {INSTALLATION[name]}"
        else:
            answer[name] = "UNDECIDED"
    return answer


def _plain(value: Any) -> Any:
    """JSON that survives the round trip, without inventing precision."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # As a string: a Decimal became a float here would change the number,
        # and this file exists so numbers do not change.
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return None
    return value


def gather(cur, project_id: str) -> dict[str, Any]:
    """Everything recorded about one project, ordered so two runs match."""
    cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
    project = cur.fetchone()
    if not project:
        raise LookupError(f"Unknown project: {project_id}")

    tables: dict[str, list[dict[str, Any]]] = {}
    for name in project_tables(cur):
        # `id` where the table has one, so a snapshot of an unchanged project
        # is byte-identical; the primary key is the only ordering every one of
        # these shares.
        cur.execute(
            f"SELECT * FROM {name} WHERE project_id = %s "  # noqa: S608
            f"ORDER BY {'id' if _has_id(cur, name) else 'project_id'}",
            (project_id,),
        )
        tables[name] = [
            {key: _plain(value) for key, value in row.items()}
            for row in cur.fetchall()
        ]

    # And the tables that reach the project through a parent (T171). Ordered by
    # every column, because several have no `id` and a snapshot of an unchanged
    # project should still be byte-identical.
    for name, condition in sorted(child_tables(cur).items()):
        placeholders = condition.count("%s")
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s "
            "ORDER BY ordinal_position", (name,))
        columns = ", ".join(f'"{row["column_name"]}"' for row in cur.fetchall())
        cur.execute(
            f"SELECT * FROM {name} WHERE {condition} ORDER BY {columns}",  # noqa: S608
            (project_id,) * placeholders,
        )
        tables[name] = [
            {key: _plain(value) for key, value in row.items()}
            for row in cur.fetchall()
        ]

    return {
        "format_version": FORMAT_VERSION,
        "project": {key: _plain(value) for key, value in project.items()},
        "tables": tables,
        "excluded": EXCLUDED,
        "note": (
            "An archive of one project, for keeping or for carrying to another "
            "machine. Nothing reads it back in yet, so it is not a backup you "
            "can restore from — the tables and their rows are here to be read, "
            "and the files this project ingested travel beside it."
        ),
    }


def _has_id(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
        "AND table_name = %s AND column_name = 'id'", (table,))
    return cur.fetchone() is not None


def as_json(cur, project_id: str) -> str:
    return json.dumps(gather(cur, project_id), indent=2, sort_keys=True,
                      default=str) + "\n"


def files_in(cur, project_id: str) -> list[dict[str, Any]]:
    """The stored files this project ingested, so the archive is self-contained."""
    cur.execute(
        "SELECT DISTINCT f.id, f.storage_key, f.filename, f.size_bytes, "
        "       f.content_hash "
        "FROM files f WHERE f.project_id = %s ORDER BY f.id", (project_id,))
    return [dict(row) for row in cur.fetchall()]
