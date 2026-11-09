# Report Template — guided-research

Skeleton and formatting patterns for the final report (Step 7 of the skill).
The report itself is written in the USER'S language; section titles below are
functional placeholders, not literal headings.

## Pyramid principle

Conclusions first, evidence after. Every section opens with its takeaway, then
proves it. The reader should get the full picture from headings and the first
lines alone.

## Skeleton

```markdown
# <Report title — the fixed goal, one sentence>

> Goal: <the one-sentence goal from Step 1, verbatim>
> Date: <YYYY-MM-DD> · Sources emphasis: <chosen in Step 5> · Style: <Step 4>

## 1. Key conclusions            ← 3–7 bullets, each answerable to the goal
## 2. <Evidence section A>       ← structure approved in Step 3, not this sample
## 3. <Evidence section B>
...
## N. What was NOT found        ← honest gaps: questions with no data
## Sources                       ← numbered list of every URL used
```

Sections come from the structure the user approved in Step 3 — this skeleton only
fixes the mandatory frame: goal verbatim on top, conclusions before evidence,
explicit "not found" section, numbered sources.

## Characteristic blocks (demo-fragment candidates for Step 3)

Comparison table:

```markdown
| Player | Positioning | Price | Strength (source) | Weakness (source) |
|---|---|---|---|---|
| ... | ... | ... | "..." [3] | "..." [7] |
```

Finding with source:

```markdown
- **Finding.** <one-sentence claim> — "<verbatim quote>", <author/platform>, [N].
```

Conclusion with justification:

```markdown
**Conclusion.** <claim>. Why: <evidence 1> [N], <evidence 2> [N], <evidence 3> [N].
```

## Style variants (Step 4)

- **Pyramid + tables + diagrams** (default): full skeleton above, tables for any
  2+2 comparison, ASCII/mermaid diagrams where they compress information.
- **Short summary (1–2 pages)**: skeleton collapses to Goal → Key conclusions →
  one evidence section → Sources; every section ≤ 10 lines.
- **Maximum-depth longread**: full skeleton plus intermediate conclusions per
  section and a "open questions" block; length follows content, never padding.

## Checklist before saving (mirrors the skill's hard-wired list)

- [ ] The report answers the fixed goal (Step 1) — verified verbatim.
- [ ] Every conclusion and recommendation answers "how did we get here".
- [ ] Every figure and quote has a source; quotes are undistorted.
- [ ] The pyramid is respected; there are tables and, where fitting, diagrams.
- [ ] The structure is the one the user approved in Step 3.
