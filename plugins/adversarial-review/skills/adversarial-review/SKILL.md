---
name: adversarial-review
description: Adversarial AI code/plan review. An independent reviewer subagent (a different model via BYOK) challenges the change; the lead agent evaluates findings, fixes, and resubmits until approved. Auto-detects plan/code/code-vs-plan mode. Use when the user asks for "adversarial review", "ревью кода", "ревью плана", or review of a diff/plan before merging.
user_invocable: true
---

# Adversarial Code Review (ZCode port, variant B)

> **Platform:** ZCode only. This skill orchestrates lead ↔ reviewer interaction, where the lead agent (you) is the executor and a custom reviewer subagent (`adversarial-review:adversarial-reviewer`, a different model configured via BYOK) is the external reviewer. The skill, its command and the reviewer agents are delivered together by the `adversarial-review` plugin — plugin agents are addressable with the `<plugin>:<name>` prefix. There is no Codex CLI, no bwrap, no external harness — the reviewer is a native ZCode subagent whose exhaustive tool list (`Read, Grep, Glob`) makes it hard read-only. Do NOT run this skill from the reviewer subagent itself.

Sends current work for adversarial review through an independent reviewer subagent. Auto-detects what to review: **plan** or **code**. The lead fixes issues based on reviewer feedback and resubmits until approved. Maximum 5 rounds.

**Variant B trade-off (vs the original Codex-based skill):** the reviewer has no persistent session and no `resume`. Every round is a fresh dispatch; rounds 2+ carry the full review history inside the prompt (Step 7). This costs tokens per round and buys independence — the reviewer never inherits the lead's self-justifications, only the verbatim record.

---

## When to invoke

- `/adversarial-review` — auto-detect what to review
- `/adversarial-review plan` — force plan review
- `/adversarial-review code` — force code review
- `/adversarial-review <file-path>` — review a specific file (argument contains `/` or `.`)
- Override reasoning effort: `/adversarial-review xhigh` or `/adversarial-review medium` (one of: `low`, `medium`, `high`, `xhigh`). Advisory only — the effort level is passed to the reviewer inside the dispatch prompt; the reviewer's own thought level is fixed in its agent file.
- Override reviewer permissions: `/adversarial-review sandbox:read-only` (default) or `/adversarial-review sandbox:workspace-write`.

Overrides can be combined: `/adversarial-review plan xhigh sandbox:read-only`.

**Arguments removed vs the original skill** (surface a one-line "ignoring unknown argument" warning if they appear, then continue):
- `model:<name>` — the reviewer model is fixed in the reviewer agent files shipped with the plugin (`agents/adversarial-reviewer*.md` in the plugin source); changing it is a one-line edit in that file (then refresh the marketplace and update the plugin), not a per-invocation argument.
- `approvals:*`, `sandbox:danger-full-access`, `sandbox:inherit` — Codex CLI concepts with no ZCode equivalent.

## Instructions

> **Placeholders:** `${REVIEW_ID}`, `${REPO_ROOT}`, and `${BASE_BRANCH}` in the steps below are template placeholders, NOT shell variables. Substitute literal values directly into each tool call. In particular:
> - `${REPO_ROOT}` is ALWAYS an absolute path captured at Step 2; never replace it with `$(pwd)`.
> - `${REVIEW_ID}` is stable for the entire review (used in file paths).
>
> There is no `ATTEMPT_ID` and no session id in this port — the reviewer is stateless between rounds.

### Step 1: Determine review mode

Determine what to review. Check in priority order:

**1. Explicit argument** (`plan`, `code`, file path) → use it.
   - For `plan` → skip all git checks, proceed to step 2 (REVIEW_ID only).

**2. Plan Mode** — if context contains the system message "Plan mode is active" → mode = `plan`, skip git. In Plan Mode code is not edited, so code/code-vs-plan are impossible.

**3. Auto-detect** (no explicit argument, not in Plan Mode):

1. Check for code changes (any non-empty output means changes exist):
   - `git diff --name-only` — unstaged
   - `git diff --cached --name-only` — staged
   - `git diff --name-only ${BASE_BRANCH}...HEAD` — branch commits
2. Check if a plan exists in the current conversation context (from plan mode, tasks, or discussion).

| Code changes? | Plan in context? | Mode |
|--------------|-----------------|------|
| No | Yes | **plan** — review the plan |
| Yes | Yes | **code-vs-plan** — review implementation against plan |
| Yes | No | **code** — review code changes |
| No | No | Ask the user what to review |

#### Step 1 (continued): Capture overrides, detect operator language, emit runtime hint

**Parse the invocation arguments for overrides** (case-sensitive on the prefix). Apply them to the per-review configuration captured below. Any unrecognized token after stripping the mode and overrides is treated as an unknown argument — surface a one-line "ignoring unknown argument: <token>" warning and continue.

| Argument shape                      | Sets                                                              | Default if absent |
|-------------------------------------|-------------------------------------------------------------------|-------------------|
| `low` / `medium` / `high` / `xhigh` | `REASONING_EFFORT = <value>` (advisory, forwarded in dispatch prompt) | `REASONING_EFFORT = high` |
| `sandbox:read-only`                 | `REVIEWER_AGENT = adversarial-review:adversarial-reviewer` (tools: Read, Grep, Glob) | (same as default) |
| `sandbox:workspace-write`           | `REVIEWER_AGENT = adversarial-review:adversarial-reviewer-exec` (adds Bash for verify commands) | n/a — explicit-only |

**No silent fallback** may change reviewer semantics. If the configured reviewer model is unavailable (the Agent tool dispatch fails with a model/provider error), abort with an explicit diagnostic (see Step 4 result handling) — do NOT substitute the built-in `general-purpose` agent or the session model silently.

**Detect operator language.** Inspect the last few human-authored messages in the current conversation. If they are predominantly in a non-English language, capture `OPERATOR_LANGUAGE = <name of language>` (e.g. `Russian`, `Spanish`, `Japanese`). If detection is ambiguous, default to `OPERATOR_LANGUAGE = English`. Runtime prose shown to the operator (warnings, summaries, intermediate updates) MUST use `OPERATOR_LANGUAGE` when practical. `OPERATOR_LANGUAGE` is also forwarded to the reviewer in every dispatch — the review prose is written in the operator's language, machine-readable literals stay English (the reviewer agent file enforces this). Skill repository files stay in English regardless.

**Emit the one-line runtime hint** about reviewer permissions — **once per review, before Step 2 starts**. The hint is suppressed when the operator passed an explicit `sandbox:*` override:

```
ℹ read-only reviewer in effect; pass sandbox:workspace-write to let the reviewer run verify commands (tests, linters, build) — mutation-snapshots then guard the round.
```

Translate the hint into `OPERATOR_LANGUAGE` if non-English. Do NOT repeat per round.

The conditional warning about already-dirty tracked files lives in Step 2 (it depends on a captured `REPO_ROOT`).

### Step 2: Generate REVIEW_ID, capture REPO_ROOT, determine base branch

**REVIEW_ID:** generate yourself, format `{unix_timestamp}-{random_8digit_number}`.
Example: `1711872000-48217593`. **Do NOT use bash** — substitute the value directly into commands in the following steps. 8-digit random makes collisions negligible (1 in 10^8 per same-second invocation).

**Capture REPO_ROOT:**

```bash
git rev-parse --show-toplevel
```

- **Exit 0, non-empty output** → absolute path. Save literally as `REPO_ROOT` (a template placeholder — substitute verbatim; do NOT use `$(pwd)` anywhere).
- **Exit 128** (bare repo, or not in a work tree) → tell the user: `Cannot run adversarial review — current directory is not inside a git working tree.` Abort the skill.
- **Path contains single quote, double quote, `$`, backtick, newline** → tell the user: `REPO_ROOT path contains shell-special characters; cannot safely construct commands.` Abort.

**Submodule warning:** after capturing REPO_ROOT, run:

```bash
git rev-parse --show-superproject-working-tree
```

If this returns non-empty, the user is inside a git submodule. Tell the user: `You are inside a submodule. The review will be scoped to this submodule (${REPO_ROOT}), not the parent repo. If you meant to review the parent, invoke from there.` Proceed — this is a warning, not an abort.

**Conditional warning about already-dirty tracked files** (skip when mode=`plan`). Now that `REPO_ROOT` is captured and validated, run `git -C "${REPO_ROOT}" status --porcelain` and inspect the output. If any line matches `^[ M][M ] ` (a tracked file already modified in working tree or index pre-review), emit this extra one-line warning (once per review):

```
ℹ <N> unstaged or staged tracked-file edit(s) detected. The porcelain mutation snapshot only detects status transitions; reviewer-side content drift of already-dirty files is NOT auto-caught. Commit/stash WIP first for stronger protection.
```

Translate to `OPERATOR_LANGUAGE`. Substitute `<N>` with the count of matching lines. Suppressed when no already-dirty tracked files exist.

**Determining base branch (only for `code` and `code-vs-plan` modes):**

For `plan` mode — skip base branch detection, proceed to step 3.

For other modes, determine the repository's base branch:

```bash
git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'
```

If the command returns empty (remote HEAD not configured), use fallback:

```bash
git rev-parse --verify main 2>/dev/null && echo main || echo master
```

Save the result as `BASE_BRANCH` — used in `git diff ${BASE_BRANCH}...HEAD` below.

### Step 3: Prepare review material

**Plan review:**

- If the plan already exists as a file (plan file from Plan Mode, task context, memory, or somewhere in the repo) — use the path directly. Do NOT copy.
- If the plan is only in the conversation context — write via **Write tool** to `/tmp/ar-plan-${REVIEW_ID}.md`. (In Plan Mode prefer the plan file that already exists on disk; if the context-only plan must be written and the write is blocked, surface the limitation and abort — do not silently skip the file.)
- **Always print the plan file path for the user** so they can open it in their IDE:
  `Plan for review: <file-path>`

**Code review:**

Collect the list of changed files:

1. `git diff --name-only` — unstaged changes
2. `git diff --cached --name-only` — staged changes

Merge unstaged + staged (unique paths). If both are empty:

3. `git diff --name-only ${BASE_BRANCH}...HEAD` — branch commits (fallback)

**Materialize the diff for the reviewer.** The default reviewer (`adversarial-reviewer`) has no Bash and cannot run `git diff` itself — the lead writes the diff to disk once per review; the reviewer Reads it:

- Local changes present (unstaged and/or staged):
  ```bash
  git -C "${REPO_ROOT}" diff HEAD > /tmp/ar-diff-${REVIEW_ID}.patch
  ```
- No local changes (branch review):
  ```bash
  git -C "${REPO_ROOT}" diff ${BASE_BRANCH}...HEAD > /tmp/ar-diff-${REVIEW_ID}.patch
  ```

The current file contents do NOT go into the diff file — the reviewer Reads changed files directly under `REPO_ROOT` (it has Read/Grep/Glob over the whole tree).

**Many files (> 50):** if the combined list exceeds 50 paths, prepend a `--stat` summary to the diff file (rewrite it as `git diff [--cached] --stat` output followed by the full diff), keep the file list out of the prompt body, and recommend `sandbox:workspace-write` (the exec reviewer can run its own targeted `git diff <path>` commands instead of Reading a huge file).

If the diff file exceeds ~2 MB, warn the operator: `⚠ Diff is very large (<size>); the review may be shallow or hit context limits. Consider reviewing in smaller batches.` Proceed anyway.

If all sources are empty — no changes to review, inform the user.

**Code-vs-plan review:** prepare the plan path AND the diff file + changed-file list (as above).

### Step 4: Build the prompt body, dispatch the reviewer subagent

Main thread composes the round BODY: the task section for the review mode (round 1) or the task + history sections (rounds 2+, see Step 7). Templates live in this skill's `references/prompts.md` — resolve and Read it ONCE per review, in this priority order:

1. **This skill's own base directory** — the session's skill list shows this skill's `SKILL.md` file path; strip the trailing `SKILL.md` and append `references/prompts.md`.
2. **Plugin cache** (how this skill is normally installed): `ls ~/.zcode/cli/plugins/cache/*/*/skills/adversarial-review/references/prompts.md 2>/dev/null | head -1`
3. **User-scope install** (fallback): `ls ~/.zcode/skills/adversarial-review/references/prompts.md 2>/dev/null`
4. Abort: `Could not locate references/prompts.md (skill base dir, plugin cache, ~/.zcode/skills/adversarial-review/). Re-install the adversarial-review plugin.`

**Substitute template placeholders BEFORE writing to disk.** The exact substitution table is in `references/prompts.md`. Key placeholders: `<plan-path>` (Step 3), `<diff-path>` (`/tmp/ar-diff-${REVIEW_ID}.patch`), `<file list from --name-only>`, `<changes description>`, `${BASE_BRANCH}`. No template placeholders should remain in the body file sent to the reviewer.

The stable reviewer role (stance, finding bar, calibration, output format, permissions) lives in the reviewer's agent file — do NOT inline it into the body. The body carries only the task and history.

**Write the prompt body to disk via Write tool:**

Write `/tmp/ar-body-${REVIEW_ID}.md` containing the substituted body text. For rounds 2+ the same path is overwritten (see Step 7).

> **Plan Mode note:** in Plan Mode writes to `/tmp` are blocked and cleanup is skipped. In Plan Mode: do NOT write `ar-body`/`ar-plan`/`ar-diff` files; keep the body in memory and pass it INLINE in the dispatch prompt (below); skip the mutation snapshots in this step (the read-only reviewer cannot write anything, so there is nothing to detect). The diff for a plan review is not needed; `code`/`code-vs-plan` are impossible in Plan Mode anyway.

**Workspace mutation snapshot (pre-dispatch)** — skip entirely in Plan Mode. Capture both layers BEFORE every reviewer dispatch (every round):

```bash
git -C "${REPO_ROOT}" status --porcelain > /tmp/ar-git-pre-${REVIEW_ID}.txt
sha256sum /tmp/ar-body-${REVIEW_ID}.md \
          /tmp/ar-plan-${REVIEW_ID}.md \
          /tmp/ar-diff-${REVIEW_ID}.patch 2>/dev/null \
  > /tmp/ar-inputs-pre-${REVIEW_ID}.sha
```

The `2>/dev/null` is deliberate: not every review has all three files (plan reviews skip the diff file, Plan Mode skips all of them).

**Dispatch the reviewer subagent via Agent tool:**

- `subagent_type: "${REVIEWER_AGENT}"` (`adversarial-review:adversarial-reviewer` or `adversarial-review:adversarial-reviewer-exec` — captured at Step 1; the `adversarial-review:` prefix is the plugin namespace)
- `description: "Adversarial-review reviewer, round N"` (N is the current round number)
- `prompt:` a short bootstrap + YAML input block. Outside Plan Mode:

```
Perform adversarial review round N per your agent instructions.

---
REVIEW_ID: 1711872000-48217593
REPO_ROOT: /home/user/sources/myproject
REVIEW_MODE: plan | code | code-vs-plan
REVIEW_ROUND: 1
REASONING_EFFORT: high
OPERATOR_LANGUAGE: English
PROMPT_BODY_PATH: /tmp/ar-body-1711872000-48217593.md
---
```

In Plan Mode, replace `PROMPT_BODY_PATH` with `PROMPT_BODY_INLINE:` followed by the full body text, indented under the block.

Substitute real values for every placeholder. **Do NOT run the Agent tool call in background.** Wait for the subagent to return — the review is the subagent's final message.

**Handle the dispatch result.** The reviewer's FINAL message must be the complete review document (Summary / Findings / Verdict, last line `VERDICT: APPROVED` or `VERDICT: REVISE`). Apply these checks in order:

1. **Agent tool call errored, or the result is empty / clearly not a review** (an error string, a refusal, a bare acknowledgment) → `agent_error`.
2. **Sanity checks on the returned text** (it must pass all):
   - Non-empty and longer than a placeholder stub.
   - Contains a line exactly matching `^VERDICT: (APPROVED|REVISE)$`.
   - If verdict is `REVISE` → the text must also contain at least one line matching `\[severity:\s*(critical|high|medium)`.
   - The substantive content is an actual review — NOT an inability report (body < ~500 chars, or dominated by "unable to perform the review" / "rate limit" / "cannot access" style statements without findings).
   Any check fails → `format_invalid`.
3. **Checks pass** → Write the returned review text **verbatim** (byte-for-byte, no rephrasing, no fence-stripping beyond the exact final-message payload) to `/tmp/ar-review-${REVIEW_ID}.md` via Write tool. In Plan Mode, skip the file write — keep the review text in context.

**Retry rule (one per round):** on `agent_error` or `format_invalid`, show the user the raw subagent output, then re-dispatch Step 4 ONCE for the same round. The retry does NOT consume the round counter — the counter advances only when a valid review is produced. Track the retry counter in the current round's reasoning only; it resets every new round. After a failed retry:

- Round 1 → **terminal ABORT** (environmental failure before any valid review): show the raw output and diagnostic, exit via Step 8 "Aborted" state, leave temp files for diagnostics.
- Round ≥ 2 → **terminal NOT VERIFIED**: fixes from prior rounds are applied but the reviewer did not re-verify; exit via Step 8 "Not verified" state.

A dispatch failure that names the reviewer model/provider (model not found, provider unavailable), or reports the agent type as unknown (`adversarial-review:adversarial-reviewer not found`), is an `infra` abort at ANY round: `Reviewer agent/model unavailable — check that the adversarial-review plugin is installed and enabled, the model: field in its agents/adversarial-reviewer*.md, and the BYOK provider config. No silent fallback to another reviewer.` Do NOT retry it as format_invalid.

**Workspace mutation snapshot (post-dispatch)** — skip in Plan Mode. BEFORE consulting the verdict and BEFORE applying any fixes, capture the post-state and diff against the pre-state:

```bash
git -C "${REPO_ROOT}" status --porcelain > /tmp/ar-git-post-${REVIEW_ID}.txt
sha256sum /tmp/ar-body-${REVIEW_ID}.md \
          /tmp/ar-plan-${REVIEW_ID}.md \
          /tmp/ar-diff-${REVIEW_ID}.patch 2>/dev/null \
  > /tmp/ar-inputs-post-${REVIEW_ID}.sha

diff -q /tmp/ar-git-pre-${REVIEW_ID}.txt /tmp/ar-git-post-${REVIEW_ID}.txt
diff -q /tmp/ar-inputs-pre-${REVIEW_ID}.sha /tmp/ar-inputs-post-${REVIEW_ID}.sha
```

Two diffs, four cases:

1. **Tracked-file mutation** — the `git status --porcelain` diff shows new modified (`^[ M]M`) or deleted (`^[ D]D`) entries that didn't exist pre-dispatch, OR untracked files (`^\?\?`) that look like edits to real source files (not editor scratchpads). **HARD STOP** before applying any fixes:

   ```
   ❌ Reviewer mutated tracked files during dispatch.
   Pre-state vs post-state diff:
   <output of `diff` on the two porcelain files>
   Aborting before fixes. Inspect the diff and decide whether to revert
   or keep the changes manually. Re-run /adversarial-review when ready.
   ```

   Then skip Steps 5–9 (except Step 9 abort-path behavior) and exit.

2. **Untracked generated artifacts only** — `^\?\?` entries that look benign (build caches, log files). Warn the operator but continue:

   ```
   ⚠ Reviewer left untracked files behind: <list>.
   Proceeding with the round, but please review whether these should be
   .gitignored or removed.
   ```

3. **Review-input mutation** — the `sha256sum` diff is non-empty. Treat like tracked-file mutation: hard stop. The reviewer must never modify its own prompt body, plan file, or diff file.

4. **No mutation** — both diffs are empty. Proceed.

The pre/post snapshot pair repeats for EVERY round dispatch. It is NOT optional — with the read-only reviewer it is expected to always come back clean (defense in depth); with the exec reviewer (`sandbox:workspace-write`) it is the only detection of reviewer-side mutation that does not depend on the reviewer self-reporting.

### Step 5: Show the review, then check the verdict

**1. The review text is already in your context** (the subagent's final message; the `/tmp/ar-review-${REVIEW_ID}.md` copy exists for the record and for cleanup semantics — Read it back ONLY if context compaction is suspected to have altered it).

**2. Show the review to the user. This is mandatory and blocking.**

> Your next user-visible message that is NOT a one-line `⚠ <warning>` diagnostic must begin with the header below, followed by the review text **verbatim**. Not "I've received the review", not "The reviewer said:", not a summary — the literal review content.
>
> Do NOT wrap the review in a code fence (the review is already markdown, and an outer fence would break on inner fences).
>
> Do NOT call any Edit, Write, or fix-applying tool in the same message as the review. The review output is a standalone user-visible message.

Message format:

```
## Adversarial Review — Round N (mode: <plan|code|code-vs-plan>, reviewer: <REVIEWER_AGENT>)

<verbatim review text>
```

**3. Only AFTER the review message has been sent** — parse the VERDICT line and dispatch:

- `VERDICT: APPROVED` → Step 8 (Done).
- `VERDICT: REVISE` → Step 6 (Fixes).
- Maximum rounds reached (5 rounds) → Step 8 with the max-rounds note.

**4. Compute the severity tally for Step 6.5** (cheap, one command — replaces the original runner triage):

```bash
grep -Ec '^\s*#{1,6}\s*\[severity:\s*(critical|high|medium)' /tmp/ar-review-${REVIEW_ID}.md
```

(Or count by eye from the verbatim review already in context. Record per-severity counts for the round: `critical N, high M, medium K`.) In Plan Mode there is no review file — count from context.

### Step 6: Evaluate findings, gate structural fixes, then apply

> **Precondition gate (check first).** Before calling any Edit, Write, or other fix-applying tool: confirm that you have already sent a user-visible message in THIS round whose body contains the verbatim review text (short `⚠ <warning>` diagnostic messages do NOT count). If you have not — STOP. Go back to Step 5 and send the review message now.

**External feedback = suggestions to evaluate, not orders to follow.** Reviewer findings are inputs to your decision; applying them blindly causes real damage when the reviewer is technically wrong (e.g. a critical "bug" that is actually a feature request, or a security flag based on a misread of the threat model). Key principles (inlined from `superpowers:receiving-code-review` for portability):

#### Step 6.1: Build the evaluation matrix

For each finding in the verbatim review, fill out one row:

| # | Severity | Verified?                          | Type            | Action               |
|---|----------|------------------------------------|-----------------|----------------------|
| 1 | high     | ✓ docs confirm behavior            | architectural   | accept               |
| 2 | critical | ✗ cited issue is feature request   | tool-mechanic   | reject with reasoning|
| 3 | medium   | ✓ small repro confirms             | tool-mechanic   | accept               |
| 4 | medium   | re-scoped to docs-only fix         | architectural   | re-scope             |

`Action` options are EQUAL first-class outcomes:

- **`accept`** — finding is valid as stated; apply the proposed fix (or a minimal variant that resolves it).
- **`reject with reasoning`** — finding is technically wrong, out of scope, or contradicts an explicit user requirement; do NOT apply, and prepare a technical counter-argument for the next round's body (Step 7).
- **`re-scope`** — finding is partially valid; apply a narrower fix than the reviewer proposed, and explain the narrowing in the next round's body.

**Verification methods by finding type:**

| Finding type                                                              | What constitutes verification |
|---------------------------------------------------------------------------|-------------------------------|
| Architectural / design                                                    | Reasoning + codebase grep, plus a pattern check against existing code |
| Tool-mechanic (DSL syntax, config parser, API contract, library behavior) | **Empirical test on the real system** — reasoning alone is not enough. Open cited upstream issues by URL. |
| Style / convention                                                        | Match against actual codebase conventions |
| Security                                                                  | Reasoning + concrete threat model |

Tool-mechanic findings are the most dangerous to accept on reasoning alone — mental models of obscure tools are often wrong. If the reviewer cites an upstream issue or doc, **open it**; citations age, issues get reclassified, and the cited number may not describe what the reviewer thinks it does.

Receiving-feedback principles:

- Read every finding end-to-end before reacting.
- Restate each finding's technical claim in your own words (mentally — don't pad the response with it).
- Verify against codebase / docs / a quick run before accepting.
- Push back when wrong, with technical reasoning, not deference.
- No performative agreement ("you're absolutely right" is a violation of the discipline).
- Skip thanks. State the fix or the reasoning.

#### Step 6.2: Classify accepted/re-scoped fixes as structural vs non-structural

After every finding has an `Action`, walk the matrix once and classify each `accept` / `re-scope` row as **structural** or **non-structural**. `reject with reasoning` rows skip this step (nothing is applied).

**Structural fixes** — pause the operator before applying:

- Invocation grammar or argument semantics (new/removed arg, renamed mode, changed value semantics).
- Output format or parsed literals (e.g. the `VERDICT: APPROVED|REVISE` line, severity tags, section headers, named workflow states).
- Workflow steps, fallback semantics, or terminal states.
- Security guarantees (reviewer permissions, mutation-snapshot semantics).
- Public configuration semantics (what an override does).
- Schema, migration, or data format changes.
- Broad architectural rewrites.
- Any fix whose scope you are uncertain about — when in doubt, classify as structural.

**Non-structural fixes** — apply without pausing:

- Wording / phrasing changes that do not change semantics.
- Correcting factual inaccuracies (wrong API name, wrong tool mechanic, wrong attribution).
- Removing outdated comments or examples.
- Adding clarifying sentences or examples that do not change observable behavior.
- Internal heuristic refinements with no externally visible effect.

#### Step 6.3: Structural operator gate (one pause per round)

Apply the **batch-pause rule**: do NOT pause once per fix. Walk the entire matrix first; then:

- If structural count is **zero** → apply everything (non-structural accepts + re-scopes) without pausing. No operator gate needed.
- If structural count is **≥ 1** AND the operator is present (a direct human message exists earlier in this session AND the host exposes a user-facing channel) → make exactly **one** pause showing:

  ```
  ### Round N — structural fixes pending operator sign-off

  **Structural (need go/no-go):**
  - [#N — one-line description]
  - ...

  **Non-structural (will auto-apply):**
  - [#M — one-line description]
  - ...

  **Rejected with reasoning (informational):**
  - [#K — one-line description]
  - ...

  Approve the structural batch? (yes / no / select specific items)
  ```

  Wait for operator response before applying any structural fix. Non-structural fixes still auto-apply.

- If structural count is **≥ 1** AND the operator explicitly requested autonomous mode (e.g. an explicit "go ahead without asking" earlier in the conversation) → apply all structural fixes without pausing, but record this fact for the final operator summary (Step 8).
- If structural count is **≥ 1** AND no operator is reachable (headless / scheduled run with no explicit autonomous flag) → apply structural fixes anyway and record the same "applied without sign-off" note for Step 8. Refusing to apply would leave the artifact half-fixed; the operator reviews after the fact.

#### Step 6.4: Apply fixes

For each `accept` and `re-scope` row:

- **Plan review** — update the plan file (or temp file). Make the change minimal: address the specific finding, don't refactor surrounding sections.
- **Code review** — edit files, run tests if applicable, run a build if the change is non-trivial.
- **Code-vs-plan** — update whichever side is wrong (plan or code), per the matrix.

**Verify your own technical claims before publishing them.** When a fix or the next-round body makes a claim about tool mechanics (DSL syntax, config parser, API contract, library behavior):

- If a quick test is possible, run it (a small repro, `docker run …`, a real database container) — not "I think this works".
- If a quick test is not possible, frame the claim as a hypothesis ("seems to", "needs verification") rather than as fact.

**Skip** a fix if it contradicts an explicit user requirement — note this in the next-round body with reasoning, not silent omission.

Show the user a brief account:

```
### Round N fixes
- Applied: [#1 — what changed, 1 line]
- Re-scoped: [#3 — what changed, why narrower]
- Rejected: [#2 — short reason; full reasoning goes to the reviewer next round]
```

#### Step 6.5: Severity-decline soft signal

After applying, glance at the round-by-round severity trajectory (from the Step 5 tallies). Expect severity to decline across rounds:

```
R1: 3 critical, 6 high, 5 medium       (typical opening)
R2: 1 high, 1 medium, 3 low            (good)
R3: 1 high                             (closing in)
R4: APPROVED                           (terminal)
```

If severity stays flat (e.g. `high → high → high` across three consecutive rounds), something is structurally off — the lead may not understand the technology, the reviewer may be looping on the same misunderstanding (possible with a stateless reviewer re-reading the same history), or the artifact has a deep problem that surface fixes can't reach. Pause and surface to the operator:

```
⚠ Severity has stayed at <level> for <N> rounds. This usually means
either (a) the artifact has a structural problem the current fixes
are not addressing, or (b) the reviewer is misreading something the
lead and reviewer disagree about. Continue, switch approach, or
abort?
```

This is a soft signal, not a hard gate. Default to continuing if the operator does not respond.

### Step 7: Resubmit (Rounds 2-5) — full-history dispatch

**There is no resume in this port.** Every round 2+ is a fresh reviewer dispatch whose prompt carries the complete history: the original task, every prior verbatim review, and the lead's structured evaluation. This is the fresh-exec shape from the original skill, promoted to the primary path.

**Step 7.1: Write the round body to disk.**

The body is NOT a "I applied your feedback, please re-check" note. It is a structured response that lets the reviewer (a) verify the applied fixes resolve the original findings, (b) contest the rejections with reasoning, and (c) catch new issues introduced by the fixes. Compose per the template in `references/prompts.md`, overwrite `/tmp/ar-body-${REVIEW_ID}.md` with:

- The ORIGINAL task section for the mode (round 1 body, unchanged).
- `## Previous review rounds` — for each prior round: the verbatim review + the round's fix account (`### Round N fixes`).
- The structured evaluation:

```
I've evaluated the findings of the last round.

## Applied
- [#N]: [what was changed and why, 1–2 lines]
- ...

## Re-scoped
- [#N]: [narrower scope, with reasoning for the narrowing]
- ...

## Rejected with reasoning
- [#N]: [technical reason for not applying — not "I disagree", but a concrete counter-argument the reviewer can engage with]
- ...

## Specific asks for re-review
1. Are my rejections technically valid? Where I rejected with reasoning, do you accept the counter-argument or push back?
2. Did the applied / re-scoped fixes resolve the original findings?
3. Did the fixes introduce any new issues?
```

- `## Current state of the artifact` — one short paragraph: what the artifact looks like now after the fixes.

Substitute the lists from Step 6's evaluation matrix (one bullet per finding per section). Sections with zero items can be omitted, but `## Specific asks for re-review` is always present.

The three-section shape (Applied / Re-scoped / Rejected with reasoning) gives the reviewer a chance to contest the rejections. A re-review that says "your rejection of #2 is valid; here's why" is just as useful as one that fixes new issues — both keep the loop honest.

**History fidelity.** Prior reviews are reproduced from the conversation's verbatim displays. If context compaction has made a round's details incomplete, write "round N details unavailable due to compaction" in the history section instead of inventing content.

**Step 7.2: Dispatch the reviewer for the next round.**

Same Agent tool invocation as Step 4 (`REVIEWER_AGENT` unchanged; changing reviewer permissions mid-review requires aborting and re-invoking with a fresh REVIEW_ID). Run the **pre-dispatch workspace mutation snapshot** BEFORE invoking the Agent tool. Input block:

```
---
REVIEW_ID: <same>
REPO_ROOT: <same>
REVIEW_MODE: <same>
REVIEW_ROUND: 2
REASONING_EFFORT: <same>
OPERATOR_LANGUAGE: <same>
PROMPT_BODY_PATH: /tmp/ar-body-<REVIEW_ID>.md
---
```

**Step 7.3: Handle the result exactly as Step 4** (sanity checks → verbatim copy to `/tmp/ar-review-${REVIEW_ID}.md` (overwrite) → post-dispatch mutation snapshot → hard stops). Then Step 5 (verbatim display), Step 6 (matrix). The one-retry rule per round applies unchanged.

**Round-level attempt invariant:** exactly ONE reviewer dispatch per round plus at most ONE retry on `agent_error`/`format_invalid`. The retry does not consume the round counter. Repeated dispatch failure in round ≥ 2 terminates as NOT VERIFIED (fixes applied, reviewer did not re-verify) — there is no deeper fallback chain in variant B.

### Step 8: Final result + operator summary

Every terminal state emits TWO messages, in this order:

1. **Per-state header block** (templates below) — the canonical "what happened" framing in English.
2. **Operator summary** — a separate operator-facing summary in `OPERATOR_LANGUAGE` (captured in Step 1; English by default). The summary comes AFTER the final verbatim reviewer response (if any) and does NOT replace it.

#### Terminal state templates

**Approved:**
```
## Adversarial Review — Summary (mode: <mode>, reviewer: <REVIEWER_AGENT>)

**Status:** Approved after N round(s)

[Final review]

---
**Reviewed and approved by the reviewer. Awaiting your decision.**
```

**Maximum rounds reached:**
```
## Adversarial Review — Summary (mode: <mode>, reviewer: <REVIEWER_AGENT>)

**Status:** Maximum reached (5 rounds) — not fully approved

**Remaining findings:**
[Unresolved issues from the last round]

---
**The reviewer still has findings. Please review them and decide how to proceed.**
```

**Not verified** (dispatch failed after retry in round ≥ 2, so fixes were applied but never re-verified):
```
## Adversarial Review — Summary (mode: <mode>, reviewer: <REVIEWER_AGENT>)

**Status:** NOT VERIFIED — fixes applied, reviewer did not re-verify

**Last round's findings:**
[Verbatim findings from the last successful round]

**Applied fixes:**
[List of fixes per finding]

---
**WARNING: This is NOT an approval. Fixes were applied but never verified by the reviewer. Manual review is required before merging.**
```

**Aborted due to environmental failure** (round-1 dispatch failure, reviewer model unavailable, or a workspace-mutation hard stop):
```
## Adversarial Review — Summary (mode: <mode>, reviewer: <REVIEWER_AGENT>)

**Status:** ABORTED — environmental failure before any valid review

**Diagnostic:**
[raw subagent output / model-unavailable diagnostic / mutation diff]

---
**No review was produced. Inspect the diagnostic, check that the adversarial-review plugin is installed and the BYOK provider is reachable, then re-invoke /adversarial-review when the environment is ready.**
```

#### Operator summary (always emitted)

After the per-state header block, emit a separate operator-facing summary in `OPERATOR_LANGUAGE`. This summary is built from per-round decision summaries already shown earlier in the conversation (the matrix in Step 6.1, the fix-account in Step 6.4, the verbatim reviews in Step 5). Do NOT re-read any files beyond what Steps 4–7 already put in context.

Include:

- **Final status** — approved, maximum rounds reached, not verified, or aborted.
- **What changed across all review rounds** — a compact list of artifact changes (one bullet per file/section, not full diffs).
- **Findings applied / re-scoped / rejected** — counts per round, with one-line descriptions only for findings the operator should pay attention to (rejections, re-scopes, structural fixes).
- **Structural changes** — whether structural fixes were applied, and whether operator sign-off was obtained or was skipped due to autonomous / headless mode.
- **Verification performed and NOT performed** — what the reviewer ran vs. what the lead verified vs. what is still unverified.
- **Remaining findings or risks** — only for non-approved terminal states; otherwise omit this section.
- **Explanation of the status** — one sentence on what the status means for what the operator should do next (especially for `NOT VERIFIED` and `ABORTED`).

Constraints on the summary:

- Do NOT include full diffs.
- Do NOT repeat full reviewer findings verbatim unless an unresolved finding still matters.
- Keep it concise and operator-useful.
- If context compaction has made the per-round history incomplete, state that limitation explicitly in the summary instead of inventing details. A summary that says "round 2 details unavailable due to compaction" beats a fabricated round-2 account.

Render the summary in `OPERATOR_LANGUAGE`. Section headers and severity tags stay in English so the operator can grep them back if needed; everything else is in the operator's language.

### Step 9: Cleanup

**Conditional on terminal state:**

| Terminal state | Cleanup behavior |
|----------------|------------------|
| Approved | Remove all temp files |
| Maximum rounds reached | Remove all temp files |
| Not verified | Remove all temp files |
| Aborted (dispatch failure, infra error, mutation hard stop) | **LEAVE files in place** for diagnostics |

**In Plan Mode:** skip all cleanup — no files were written. (The inline-dispatch path creates nothing on disk.)

**Outside Plan Mode, on a cleanup-eligible terminal state:**

```bash
rm -f /tmp/ar-plan-${REVIEW_ID}.md \
      /tmp/ar-body-${REVIEW_ID}.md \
      /tmp/ar-diff-${REVIEW_ID}.patch \
      /tmp/ar-review-${REVIEW_ID}.md \
      /tmp/ar-git-pre-${REVIEW_ID}.txt \
      /tmp/ar-git-post-${REVIEW_ID}.txt \
      /tmp/ar-inputs-pre-${REVIEW_ID}.sha \
      /tmp/ar-inputs-post-${REVIEW_ID}.sha
```

If the user declined `rm` — continue without error.

Do NOT delete plan files that existed before the review (only temp files created by this skill). On abort paths, old temp files remain for diagnostics and will be cleaned up by the OS on reboot, or overwritten by the next invocation using the same REVIEW_ID (collision probability is ~10⁻⁸ per same-second run).

## Rules

- The lead **actively fixes** issues based on reviewer feedback — this is NOT just message forwarding.
- Reviewer findings are shown **verbatim** — do not rephrase or shorten. The Step 5 blocking-message contract is mandatory.
- Auto-detect review mode from context; user arguments take priority.
- With explicit `plan` argument or in Plan Mode: skip git checks and base branch detection.
- **`REPO_ROOT` is captured at Step 2** and passed as an absolute literal in every dispatch.
- **The reviewer is dispatched via Agent tool** with `subagent_type: ${REVIEWER_AGENT}` (`adversarial-review:adversarial-reviewer` — hard read-only `Read, Grep, Glob`; `adversarial-review:adversarial-reviewer-exec` — adds `Bash` for verify commands; both shipped by the adversarial-review plugin). The call is synchronous (never `run_in_background`). The reviewer's model and thought level are fixed in its agent file — there is no per-call `model` argument.
- **The review is the reviewer's final message.** The lead sanity-checks it (VERDICT line; severity tags on REVISE; not an inability report), writes it verbatim to `/tmp/ar-review-${REVIEW_ID}.md`, and displays it verbatim. The lead never fabricates or edits review content.
- **One dispatch + at most one retry per round.** Retry on `agent_error`/`format_invalid` does not consume the round counter. Repeated failure: round 1 → ABORT; round ≥ 2 → NOT VERIFIED.
- **No silent fallback.** Reviewer model unavailable → explicit infra abort. Never substitute `general-purpose`, the session model, or another reviewer agent silently.
- **Reviewer-side mutation is guarded two ways**: (a) the reviewer's exhaustive tool list (hard read-only by default; exec variant adds Bash under prompt-level prohibition), and (b) the dual-layer mutation snapshot before/after EVERY dispatch (`git status --porcelain` + `sha256sum` of `ar-body`/`ar-plan`/`ar-diff`). Tracked-file or review-input mutation is a hard stop before fixes.
- **Rounds 2+ are full-history dispatches** (original task + verbatim prior reviews + structured evaluation + current state). There is no session resume; history fidelity beats token economy. If compaction lost a round, say so in the history section.
- **Reviewer findings are suggestions to evaluate, not orders to follow.** Step 6 builds an evaluation matrix with three first-class actions: `accept`, `reject with reasoning`, `re-scope`.
- **Structural fixes need operator sign-off** unless autonomous mode was requested or no operator is reachable. Batch-pause rule: exactly one operator prompt per round.
- **Maximum 5 rounds** to protect against infinite loops.
- **Operator language** is detected at Step 1. Runtime prose uses `OPERATOR_LANGUAGE`; the review prose is written in the operator's language (forwarded in the dispatch); machine-readable literals (`[severity:]`, `VERDICT:`, Summary/Findings/Verdict headers) stay in English regardless.
- **Final operator summary** is emitted at every terminal state in `OPERATOR_LANGUAGE`, AFTER the final verbatim reviewer response.
- **In Plan Mode**: body passed inline, no `/tmp` writes, no snapshots, no cleanup, exec reviewer not used.
- Show the user reviews and fixes for each round.
- If a fix contradicts an explicit user requirement — skip it, record it in the next round's `## Rejected with reasoning` section, and surface it in the Step 8 operator summary.
- **Template location**: prompt templates are read once per review from `references/prompts.md` (resolve via this skill's base directory from the session skill list, then the plugin cache, then `~/.zcode/skills/adversarial-review/`); the reviewer role/permissions live in the agent files shipped with the plugin, never in the body.
