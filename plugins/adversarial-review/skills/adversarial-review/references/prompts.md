# Prompt templates — adversarial-review (ZCode port, variant B)

> This file is read ONCE per review by the lead agent (SKILL.md Step 4). The reviewer's stable role (stance, finding bar, calibration, output format, permissions) lives in the reviewer agent files shipped with the adversarial-review plugin (`agents/adversarial-reviewer.md` / `adversarial-reviewer-exec.md`) and is NEVER inlined into a body — the body carries only the task and history sections below.

## Round-1 bodies

### Task section — plan review

```
<task>
Review the implementation plan in <plan-path>.
</task>
```

If the plan is a file the reviewer can Read, `<plan-path>` is the absolute path (preferred). If the plan only exists in the conversation and was written to `/tmp/ar-plan-${REVIEW_ID}.md`, use that path. In Plan Mode (inline dispatch), embed the plan text itself under the task section instead of a path.

### Task section — code review (≤ 50 files)

```
<task>
Review the code changes in this repo (REPO_ROOT: ${REPO_ROOT}).

Changed files:
<file list from --name-only>

Changes include: <changes description>.
The full diff is in <diff-path> — Read it first; then Read the changed files
under ${REPO_ROOT} directly to see their current state.
</task>
```

### Task section — code review (> 50 files)

```
<task>
Review the code changes in this repo (REPO_ROOT: ${REPO_ROOT}).

Changes include: <changes description> (> 50 files).
The diff-stat summary and full diff are in <diff-path> — Read the summary
first, then inspect the changed files under ${REPO_ROOT} directly,
prioritizing security- and integrity-relevant paths.
</task>
```

### Extension — code-vs-plan review

Append to the `<task>` section (either code variant):

```
Review the code changes against the implementation plan in <plan-path>.
```

And append these items to the task (the attack-surface list itself lives in
the reviewer agent file; these are the mode-specific additions):

```
Additionally check:
- Completeness: does the implementation cover all plan steps?
- Deviations: where does the code differ from the plan? Are deviations justified?
- Missing: what from the plan is not yet implemented?
```

## Rounds 2+ — history sections

Overwrite `/tmp/ar-body-${REVIEW_ID}.md` with, in this order:

1. The **round-1 task section for the mode** (unchanged — the reviewer is stateless and needs the original task every round).
2. The history section:

```
## Previous review rounds

### Round 1
<verbatim review text of round 1>

Round 1 fixes:
- Applied: [...]
- Re-scoped: [...]
- Rejected: [...]

### Round 2
...and so on for every completed round...
```

3. The structured evaluation of the LAST round:

```
I've evaluated the findings of the last round.

## Applied
- [#N]: [what was changed and why, 1–2 lines]

## Re-scoped
- [#N]: [narrower scope, with reasoning for the narrowing]

## Rejected with reasoning
- [#N]: [technical reason for not applying — not "I disagree", but a concrete
  counter-argument the reviewer can engage with]

## Specific asks for re-review
1. Are my rejections technically valid? Where I rejected with reasoning, do you accept the counter-argument or push back?
2. Did the applied / re-scoped fixes resolve the original findings?
3. Did the fixes introduce any new issues?
```

Sections with zero items can be omitted; `## Specific asks for re-review` is always present.

4. The current state:

```
## Current state of the artifact
[one short paragraph: what the artifact looks like now after the fixes]
```

**History fidelity:** reproduce prior reviews from the conversation's verbatim displays. If compaction lost a round, write "round N details unavailable due to compaction" — never invent content.

## Substitution table

Substitute ALL placeholders before writing the body to disk — no template placeholders may remain in `/tmp/ar-body-${REVIEW_ID}.md`.

| Placeholder | Value source |
|---|---|
| `${REPO_ROOT}` | captured at Step 2 (absolute literal) |
| `${BASE_BRANCH}` | captured at Step 2 (appears inside `<changes description>` for branch reviews) |
| `<plan-path>` | captured at Step 3 |
| `<diff-path>` | `/tmp/ar-diff-${REVIEW_ID}.patch` (written at Step 3) |
| `<file list from --name-only>` | merged result of `git diff --name-only` + `git diff --cached --name-only` (or branch diff) from Step 3 — omit in the > 50 files variant |
| `<changes description>` | human-readable description derived from which diff sources had content: `unstaged changes` / `staged changes` / `unstaged + staged changes` / `branch changes vs ${BASE_BRANCH}` |

## Inline (Plan Mode) variant

In Plan Mode no `/tmp` files are written. The dispatch prompt carries `PROMPT_BODY_INLINE:` followed by the full body text. Embed the plan text itself (plan reviews) or, for file-specific reviews, the file path (the read-only reviewer can Read workspace files). Code/code-vs-plan modes do not occur in Plan Mode.
