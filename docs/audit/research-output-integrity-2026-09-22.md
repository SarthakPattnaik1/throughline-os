# Research Output Integrity Audit — 2026-09-22

This audit follows a research result from the scientific runtime through the
visualization recommendation, prepared chart data, API, live React surface,
saved visual, and publication export.

The purpose is not to claim that every visualization is correct because a test
suite is green. It is to make the contracts between layers explicit enough that
a mismatch becomes a failing test rather than a plausible-looking figure.

## Invariants checked

1. A figure refers to the exact analysis run it claims to visualize.
2. The dataset version and declarative filter population match that run.
3. Raw plotted rows use the same numeric complete-case rules as the runtime.
4. Large-data density summaries aggregate the complete filtered population
   rather than a small display sample.
5. Statistics shown on a figure come from the recorded run.
6. A renderer does not fit a second statistical model or independently
   re-aggregate a prepared summary.
7. Live browser figures and publication exports consume the same prepared
   summaries.
8. Sampling or preparation caveats travel with exported figures.
9. Saved-figure edits cannot change the data/model meaning without
   recomputation.
10. Every scientific method that advertises a visualization has a defined,
    testable path through recommendation, preparation and rendering.

## Confirmed defects corrected in this audit chain

- machine-style unit names could render duplicate units on axes;
- the live React axis dropped units that the backend/export carried;
- correlation figures added a regression fit and an unrelated visual band;
- correlation captions exposed internal statistic identifiers;
- sampling metadata was returned incompletely or hidden by the React surface;
- a brushed subset could be attached to the first project dataset rather than
  the active analysis dataset;
- simple-regression lines were re-fit from displayed/sample points instead of
  using the recorded statsmodels coefficients;
- the Vega path performed its own regression transform;
- filtered analyses could plot unfiltered rows;
- raw chart sampling did not apply the runtime's numeric complete-case rule;
- dense-correlation binning could be based on a 500-row display sample rather
  than the complete filtered population;
- result-derived plots attempted to sample synthetic fields such as
  `estimate` and `predictor`;
- surface sampling omitted the model outcome needed for observed z values;
- the live surface z label used the second predictor rather than the fitted
  outcome;
- HEXBIN could be recommended without a preparation path;
- logistic regression and mixed models had no visualization recommendation;
- box summaries, histogram bins and binned density were independently
  recomputed by renderers;
- forest, box, heatmap and histogram recommendations could be unreachable or
  fall through to the generic Cartesian path in React;
- publication exports could drop sampling/preparation caveats;
- `visual_type` could be edited on a saved visual without recomputing its
  prepared data;
- a regression-line annotation could be added to a figure whose run never fit a
  regression;
- a caller-supplied creation spec could disagree with the run, dataset,
  filters, variable roles or method-compatible chart type;
- the visual creation route could reach recommendation/sampling before scoping
  and validating the requested run;
- result-derived sampling responses had a shape that differed from the React
  contract;
- quick standalone SVG saves could lose CSS-only presentation and their visible
  caveat.

## Method-to-figure contract

| Analysis method | Default figure contract |
|---|---|
| descriptive | histogram |
| pearson_correlation | scatter; full-population binned density at large n |
| spearman_correlation | scatter; full-population binned density at large n |
| bootstrap_correlation | scatter; full-population binned density at large n |
| linear_regression, one predictor | scatter + recorded fitted line |
| linear_regression, multiple predictors | coefficient forest |
| logistic_regression | odds-ratio forest, null = 1 |
| mixed_model | fixed-effect coefficient forest, null = 0 |
| t_test | box plot |
| mann_whitney | box plot |
| anova | box plot |
| kruskal_wallis | box plot |
| chi_square | contingency heatmap |

A two-predictor linear-regression surface remains a distinct fitted-model view;
when created, its grid is evaluated from the recorded coefficients rather than
re-fit in a renderer.

## New regression barriers

- `tests/test_visual_contract_matrix.py` exercises the supported method matrix
  through recommendation → preparation → web renderer → publication renderer.
- `tests/test_figure_sampling.py` covers uniform sampling, exact filtered
  populations, all declarative filter operators, numeric complete cases, and
  full-population streaming aggregation for dense figures.
- `tests/test_visuals.py` covers immutable visual/run provenance, saved-figure
  edit boundaries, regression-fit provenance and uncertainty semantics.
- `apps/web/tests/figures.test.tsx` covers live dispatch for scatter-derived
  views plus forest, box, heatmap, histogram, sampling disclosure, run dataset
  provenance and surface outcome labeling.

## Verification status

This document records the implementation audit. The merge decision is still
gated on the repository's required clean-environment checks for the current PR
head:

- Ubuntu Python 3.12 backend suite
- macOS Python 3.12 backend suite
- Windows sandbox suite
- web tests, lint and production build
- Docker build and health check
- CodeQL

A cancelled, skipped, missing or stale run is not counted as passing evidence.
