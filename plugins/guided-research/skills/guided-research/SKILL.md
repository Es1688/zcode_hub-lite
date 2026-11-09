---
name: guided-research
description: Deep research on any topic via a fixed algorithm: guided intake dialogue first (goal, context, structure, style, sources), then multi-round web research, then a sourced .md report. For competitive analysis, audience and pain-point research, market/niche/technology analysis, content research. Trigger on "research", "deep research", "исследуй", "проведи ресерч", "проанализируй конкурентов/рынок/аудиторию", "собери боли пользователей", "изучи тему", "подготовь отчёт по…". Universal for any research task whose result is a report or recommendations. Do NOT use for one-off fact-checks (1–2 sentences) or for writing code.
user_invocable: true
---

# Guided Research (ZCode port)

> **Platform:** ZCode port of the upstream skill `guided-research` by abbnv (MIT); the
> pristine upstream snapshot lives in the hub at `guided-research-main/` and is never
> modified. Delivered by the `guided-research` plugin (skill + `/guided-research`
> command). Interactive steps use the `AskUserQuestion` tool (up to 4 questions per
> call, 2–4 options each; the user can always pick "Other" and type free text — that
> is where they edit or override any proposed formulation). Research steps use
> `WebSearch` and `WebFetch`.
>
> **Language:** run the whole dialogue with the user and write the report in the
> USER'S language (this skill body is English; the user may not be).

## Mission

Conduct deep research on the user's topic and deliver a report as `.md`. The topic can
be anything: competitors, an audience and its pains, a market, a niche, a technology,
a content theme.

Follow the algorithm below strictly. Core principle: research quality is 80%
determined by the quality of the inputs, and users rarely provide them unprompted.
So do NOT start researching immediately — first walk the user through Steps 1–5
(intake: goal, context, structure, style, sources), and only then research (Step 6)
and write the report (Step 7). In Steps 1–5 you lead the dialogue: ask questions with
answer options via `AskUserQuestion` instead of waiting for the user to formulate
everything themselves.

The result is ALWAYS a report in `.md` saved to the `reports/` folder of the current
project (create the folder if missing) with a clear name in the language of the
request, e.g. `reports/исследование_конкурентов_трекер_видео.md`. After saving, show
and hand the file to the user.

## Algorithm

Hard limit of user interruptions: 3–4 per run (goal → context on one screen →
structure+style+sources on one screen → report). More annoys the user; fewer makes
the report generic. Ask all multiple-choice questions via `AskUserQuestion`.

## Step 1. Goal — formulate it TOGETHER with the user

The most important step.

1. Ask for the research goal. A topic ("research competitors") is NOT a goal, it is
   the object. Reformulate using the formula: "I want [action/decision], so that
   [result]; for this I need to understand [what]" (in the user's language).
2. Offer 2–3 goal formulations via `AskUserQuestion` (one question, options built
   from what the user wrote). The user picks one or edits via "Other".
3. Fix the goal in ONE sentence. It becomes the report's first screen and the
   inclusion filter: anything that does not serve the goal does not go into the
   report.

Example: a bad goal — "research the coffee-shop market"; a good goal — "deciding
whether to open a coffee shop in district X; need to understand demand, competitors,
and free price niches".

## Step 2. Context — generate questions from the goal

Generate 4–6 questions FROM the fixed goal (not a fixed list). Generation rules:

- Every question must change the report's content; if an answer changes nothing —
  do not ask.
- Cover the axes relevant to this goal type: object/subject, audience,
  geography/language, constraints, facts the user already knows, "how is this solved
  right now".
- Offer 2–4 answer options per question where possible.

Ask via `AskUserQuestion`: one call holds at most 4 questions, so split 4–6 questions
across at most TWO calls (upstream's "maximum two rounds" preserved). Do not
interrogate in circles. Do not re-ask what the user already stated in their first
message.

## Step 3. Report structure + demo fragment

1. Show the report skeleton adapted to the goal: sections depend on the goal, they
   are not fixed. Base: pyramid — conclusions first, then evidence.
2. Write ONE demo fragment (2–3 lines of a characteristic block: a piece of a
   comparison table, one finding with its source, one conclusion with justification)
   and ask "is this format OK?". This is a cheap check BEFORE spending research
   time — do not skip it.

## Step 4. Style — offer options, don't ask openly

Via `AskUserQuestion` options: "pyramid + tables + diagrams" (default), "short 1–2
page summary", "maximum-depth longread".

## Step 5. Sources — ask about the emphasis

Via `AskUserQuestion` options: "real people's voice (forums, Reddit, X, review
sites, chats)", "official data, statistics, research", "what the players publish
themselves (sites, ads, content)", "all together". The choice changes the search
patterns — carry it into Step 6. Combine Steps 3–5 into ONE `AskUserQuestion` call
(three questions: format OK? / style / source emphasis).

## Step 6. Research

Run a deep-research cycle: 10+ search steps with `WebSearch` and `WebFetch`. After
each round — a short thinking + summary: what was found, what is missing, what is
next. Match search patterns to the chosen source emphasis and the market's
language/geography: for "people's voice" — queries to forums, Reddit, review sites
in the market's language; for official data — statistics and reports; for "the
players" — their sites, ads, content.

`WebSearch` limitation: it is US-only, so results may skew for non-US markets.
Compensate: refine queries (site names, transliteration, local platform names) and
`WebFetch` direct URLs of forums, review sites, and player pages found via search or
already known.

Hard rules:

- Quotes only verbatim + with source.
- Look for the players' weaknesses in negative reviews.
- Search for "how people solve the problem right now without ready-made solutions".
- Invent NOTHING: no data — say exactly that.

## Step 7. Report → .md in reports/

Assemble the report per the structure approved in Step 3, in the chosen style. First
screen — the goal in one sentence. Use the report skeleton and formatting patterns
from `references/report-template.md` (located next to this SKILL.md in the plugin;
if it cannot be resolved, build the skeleton from Step 3 and apply the checklist
below). Run the checklist, then save the file to `reports/` and hand it to the user.

Checklist before saving:

- The report answers the fixed goal (Step 1) — verify verbatim.
- Every conclusion and recommendation answers "how did we get here".
- Every figure and quote has a source; quotes are undistorted.
- The pyramid is respected; there are tables and, where fitting, diagrams.
- The structure is the one the user approved in Step 3.

## Hard-wired (never violate)

- The goal formula from Step 1 — one sentence.
- The question-generation rules from Step 2.
- The demo-fragment rule — always before the research starts.
- The quality checklist from Step 7 — before every save.
- "Verbatim quotes", "don't invent", "the result is always .md in reports/".

Everything else is regenerated anew for every task.
