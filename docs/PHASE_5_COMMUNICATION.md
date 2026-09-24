# Phase 5 — Communication and citation integrity

Per §58, §79, §80, §93 and §133.

**Status: working end to end.** A tested connection becomes a report; the report
exports to Markdown, HTML, DOCX and PPTX; every number in every format is read
from a recorded row at render time; every citation resolves to something that
was actually ingested.

## What "100% accuracy" means here, and what it does not

The request was for 100% accuracy. That is achievable for some claims and not
others, and collapsing the difference would be the most damaging thing this
phase could do. So:

### Exact by construction

**Numerical fidelity.** A block cannot store a statistic. It stores a template
with `{{ref:name}}` placeholders and a map of references, each naming a recorded
analysis run or connection and a path into it. Rendering resolves them. The
number on the page is therefore not a copy of the computed value that might have
drifted — it *is* the computed value, read when the document was produced.

`add_block` refuses any template containing a literal statistic, so this is not
a convention that authors are asked to follow. There is no field in which a
transcribed number could sit.

**Citation resolvability.** A citation is a foreign key to a passage, a source
or an analysis run, with a CHECK constraint requiring exactly one target. A
reference to a paper nobody uploaded cannot be inserted. That closes the
hallucinated-reference failure mode at the schema level rather than by review.

**Provenance completeness.** An artifact reaches its evidence through those two
mechanisms or it does not render.

### Checked, but partial — and labelled as such

**Citation entailment.** Whether a passage *supports* a sentence is a semantic
judgement, and no model provider is configured. What is decided mechanically:

- A numeric claim must have its number in the cited span, or in the cited run's
  recorded result.
- Numbers that arrived through a resolved reference are excluded, because they
  already carry a stronger guarantee than entailment could give them.
- A percentage matches the same quantity stored as a proportion — the runtime
  writes "100.0%" for a stored `1.0` — but only when the `%` is present.
- Section and figure markers (`§52`, `Table 2`) are labels, not quantities.

Everything else is `not_checkable`. An unchecked citation is `unverified`.
Neither ever reads as `supported`, and the interface shows the unchecked count
next to the checked one, because a reference list that displayed only verified
citations would imply the rest had passed.

### The one exception, and why it is still exact

Drafting a report from real data hit the literal-statistic guard: a validation
check's own detail string reads *"the coefficient on consumption_ddd is 2.542"*.
Copying that into a block is transcription.

The rule was not relaxed. It was sharpened: the rule guards against a
*generator* authoring a number, and text emitted by the sandboxed runtime and
stored in the record is the computation's own words. So a block may be marked
`quoted_from`, and the claim is **verified rather than trusted** — on insert the
named row is re-read and compared character for character. Edit one digit and it
stops being a quotation and the literal rule applies again.

The guarantee therefore holds in a stronger form: every number in a rendered
artifact is either a reference resolved from a recorded row, or text proven
identical to what a computation wrote.

## Two modelling errors found by running it

**A q-value is not a property of an analysis run.** It is a property of that test
*within its correction family* (§49 step 7) — the same run in a family of five
and a family of five hundred yields different q. It lives on the connection, and
value references reach connections for exactly this reason. Storing it on the
run would let a report quote a correction that no longer matched the family it
was corrected in.

**Entailment is not a property of a citation.** It is a property of a (claim,
citation) pair. One citation attached to three sentences was checked three times
and the last verdict overwrote the rest, so a reference printed as
`not_checkable` while supporting a sentence stating r and q. The verdict moved
onto the join, where the pair lives. The reference list now summarises across
pairs — `not_checkable, supported` — rather than showing one and implying it
covers all.

## `/evals` (§58)

```bash
python -m evals.harness --project prj_...
```

7 of the 10 §58 categories are implemented. 3 are declared and reported as
`not_implemented` with the reason, rather than omitted:

| Category | State |
|---|---|
| Numerical fidelity | structural |
| Citation resolvability | structural |
| Citation entailment | checked, partial by construction |
| Provenance completeness | checked |
| Finding classification | checked |
| Visualization fidelity | checked |
| Paper extraction | needs a labelled benchmark and a model |
| Analysis selection | methodological judgement |
| Hallucinated sources | checked against ingested DOI and notebook-link targets |
| Video claim fidelity | §136 is unbuilt |

The check that actually bites is **render staleness**. Re-resolving and
comparing against the resolver's own output cannot disagree — both read the same
row — so that alone would be a tautology. What can disagree is a file already
written: a DOCX exported last week and emailed to a co-author states the numbers
as they were then. Every stored render carries the hash of the values it was
built from, so a re-hash says exactly whether that file is still true.

Verified by perturbing a connection's `q_value` and re-running: all four exported
files were flagged as stating numbers the analyses no longer support. Restoring
the value returned the suite to green.

Attempting the same perturbation on a *completed analysis run* was refused by a
Phase 2 database trigger — a terminal run is immutable and must be forked (§12).
That path cannot go stale because it cannot change.

## Exports

Markdown, HTML, DOCX and PPTX, all from one resolved artifact (§74). No renderer
recomputes anything, so the formats cannot disagree with each other. Every
export carries two sections the screen version also shows:

- **References**, each with its entailment summary across the claims it supports.
- **How every number here was produced**, naming the row and path each value was
  read from — the §93 trail in a form that survives the file leaving the
  application, which is when LAW 5 is hardest to honour.

Rendering is refused outright when the integrity check reports problems. A file
outlives the warning that would have accompanied it on screen.

`python-pptx` was installed for this phase; `python-docx` was already present.

## Not built

- **Dashboards** (§81) — the model supports `artifact_type = 'dashboard'`, but
  no interactive dashboard surface exists.
- **Manuscripts** (§82) beyond the report structure — no journal templates, no
  LaTeX export.
- **Figures inside reports.** `artifact_blocks.visual_id` exists and is
  constrained, but no renderer embeds an image yet, so a figure block currently
  renders as its caption.
- **Editing.** Reports are drafted and exported; there is no block editor.
- Everything downstream of §133: connectors, video, dashboards.
