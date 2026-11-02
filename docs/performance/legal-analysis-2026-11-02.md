# Legal plan latency, 2 November 2026

The deployed model-only baseline for an owned synthetic consumer-dispute case was
105.72 seconds. Both actual high-reasoning profiles received the same three
approved compact reference analogies and twenty complete legal fragments. Source
preparation, Telegram delivery and initial intake were outside this model timer.
All probes were read-only; no case/report or lawyer notification was created.

The server was not CPU/memory constrained. Research occupied 85.75 seconds in the
initial probe and 94.65 seconds in its repetition, versus reviewer 19.93/16.76.
The research prompt plus input had 100,916 characters. Removing validations,
trimming normative content, changing high reasoning or swapping the model were
not attempted.

| Attempt | Total seconds | Research / review | Decision |
|---|---:|---|---|
| Deployed baseline, original | 105.72 | 85.75 / 19.93 | Baseline |
| Deployed baseline, repeated | 111.44 | 94.65 / 16.76 | Baseline |
| Prefer fewer points without a prose length guideline | 99.75 | 77.92 / 21.80 | Discarded; small gain close to variability |
| Brief practical prose, trial 1 | 82.62 | 68.91 / 13.67 | Kept after repetition |
| Brief practical prose, trial 2 | 89.14 | 76.37 / 12.73 | Kept |

The kept instruction is `_CONCISE_PLAN_STYLE`, appended immediately before the
existing fact-context/schema in both ordinary and lawyer-plan research prompts.
It asks for a practical plan, usually 2–3 LEGAL and 3–4 ACTION points with 1–2 short
sentences, without repeating the narrative or identical caveats. Its 120–300
character guideline explicitly yields to completeness: material legal conditions,
timelines, alternatives, unknown facts and burden of proof remain mandatory.
It does not truncate content, reduce existing schema limits or reject longer
verified proposals. The reader and all Core guards remain unchanged.

Both brief trials returned three LEGAL and four ACTION claims, all Core-verified,
with no clarifications. Inspection retained the conditional refund basis, patient's
choice of remedies, warranty-dependent burden of proof, taking the oral request,
examination/document checks and the conditional ten-day deadline. Input evidence,
its dates and source provenance, reference examples, independent semantic review,
provider/model/high mode and existing time budgets were unchanged.

Mean observed latency fell from 108.58 to 85.88 seconds (~20.9%); the within-pair
ranges were 5.72 and 6.52 seconds. This is two samples on one synthetic scenario,
not a p95 or a legal-quality certification. The existing 45-second target remains
open. Re-run paired real-profile probes on the same frozen context when investigating
future regressions, retain metadata-only timings and inspect output completeness;
do not infer throughput from fake model responses or mix queue/intake time into
this comparison. Private source/candidate outputs remain outside git and logs.

Validation before release: all 742 Agent tests passed. Full suite, CI and deployed
verification are recorded on the release PR, including any further measurements.
