# Maintainers

Throughline uses project-level and area-level maintainership to make technical ownership
explicit. Maintainers are expected to shape standards, review changes, and make decisions
within their stated scope. Repository permissions and branch rules still govern who can
merge changes.

## Project Co-Owners

**@SarthakPattnaik1**  
**@thephenyl02-creator**

The project co-owners jointly steward Throughline at the project level, including overall
product direction, repository governance, cross-cutting architecture, security and privacy
boundaries, release policy, maintainer appointments, and external project partnerships.

Area maintainers have real decision authority inside their documented scope. Changes that
cross into project-wide concerns require project-level review.

"Project Co-Owner" describes governance of the open-source project. It does not by itself
create or document company equity, employment, or other legal ownership rights outside
the repository.

## Reproducibility & Evaluation

**Byungwoong Yoo (@ByungwoongYoo)**  
**Role:** Reproducibility & Evaluation Maintainer

### Responsibilities

- Own the reproducibility and evaluation roadmap for Throughline.
- Define and evolve replayability, reproducibility, and evaluation acceptance criteria.
- Review changes that affect replay contracts, replay receipts, reproducible code export,
  statistical conformance, or evaluation harnesses.
- Decide whether a capability can be represented as faithfully replayable, and require
  explicit refusal when the current system cannot reproduce a recorded run honestly.
- Define the reproducibility/evaluation acceptance criteria for external research pilots.
- Propose and lead focused milestones in this area without requiring task-by-task direction.

### Decision scope

The Reproducibility & Evaluation Maintainer is the primary technical reviewer and
decision-maker for changes within this area.

Changes that also alter project-wide architecture, public API contracts, security or
privacy boundaries, persistence schemas, release policy, or other maintainer-owned areas
require the relevant additional review.

### Role boundaries

This maintainer role grants technical ownership within the scope above. It does not by
itself grant project co-ownership, equity, employment, company authority, administrative
control of the repository, access to secrets, or decision authority outside the
maintainer's documented area.

Repository access should follow least privilege. Changes to `main` should go through pull
requests, required CI, and independent review under the repository ruleset.

The operational review paths are recorded in `.github/CODEOWNERS`. As the area grows,
those paths should be updated so code ownership continues to match actual responsibility.
