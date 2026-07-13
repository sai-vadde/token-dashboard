# Agent loop rules — Token Dashboard

The main agent orchestrates work against `README.md` + `docs/` (human-owned
description) while preserving `docs/BOUNDARIES.md`. The human is the
feature-boundary QA gate and unblocker.

## Session-start read diet

Read this file, `docs/feature_log.md`, and `docs/blockers.md`. Read other files
only through the routing below. The feature log is an index, not history.

## Stack

Python 3 **stdlib only** (`http.server` + `sqlite3`) backend; vanilla JS +
ECharts frontend with **no build step**. Check entry point:
`python scripts/full_check.py`. Runs on macOS, Windows, Linux.

## Execution tier policy

**The cheapest sufficient path wins. The four agents are an à-la-carte capability
menu, not a mandatory pipeline** — spawning a subagent costs more tokens than the
main agent doing the work, so only escalate when the task actually buys
confidence from it (planning, fresh context, or independent judgment). Most work
is Tier 0/1 and never leaves the main agent. Never run a role a task doesn't need
just because it's installed; never plan a one-line fix; never spawn a reviewer
for a rename.

Classify every request before spawning (see `ORCHESTRATION_TIERS` in the
LoopKit source at `D:\Agent-loops\loopkit\core`):

- **Tier 0** — answer, diagnosis, status, doc-only: main agent, no team, no plan.
- **Tier 1 (default for real changes)** — small local change with clear intent
  and a focused check: **the main orchestrator just does it** — writes, runs
  `python scripts/full_check.py` itself, reports. No plan, no coder handoff, no
  reviewer. This is where token savings live; prefer it whenever the change fits
  one head.
- **Tier 2** — bounded slice where fresh context helps: orchestrator writes a
  one-step `step_plan.md`, invokes only `step-coder`, reads the compact check
  result. Add a reviewer **only if** risk rose during implementation.
- **Tier 3** — feature cluster, schema/migration, public `/api/*` contract,
  cross-source (Claude+Codex) behavior, or broad blast radius: `technical-planner`
  → one `step-coder` per step → verification chosen by risk (below). Tier 3 means
  "planned + independently judged," **not** "always run all four agents."

### The roles are optional and prioritized — pick the subset

Within any tier, include a role only when its trigger is real:

- `technical-planner` — only when the work is multi-step or ambiguous enough that
  a plan prevents rework. A clear single step needs no planner.
- `step-coder` — the sole writer whenever code goes through a subagent (Tier 2/3).
- `step-intent-reviewer` — only when intent could diverge from tests: public
  contract, ambiguous requirements, security, or gameable acceptance criteria.
  Skip it for mechanical, well-tested changes.
- `adversarial-tester` — only once per feature, and only for a live,
  user-facing, or security-sensitive surface. Backend-only, fully-unit-tested
  work skips it entirely (collapse into the reviewer).

### Parallel, not one-on-one serial

After `step-coder` has **stopped writing**, independent verification runs
concurrently — `step-intent-reviewer` and `adversarial-tester` in parallel, and
any independent read-heavy exploration alongside them (cap: main + two children,
depth one). Serial hand-offs are only for true dependencies (plan → code → check).
Never keep two writers going at once.

### Project tier overrides (forced, with reason)

- **Any change to `token_dashboard/db.py` schema/migrations, `pricing.json`
  structure, or an `/api/*` response shape → Tier 3.** These are frozen
  interfaces the frontend and stored DBs depend on (`docs/BOUNDARIES.md`).
- **Any change touching a shared/cross-source query or pipeline path → must run
  the source-parity check** (every affected endpoint at `source=all` *and* at a
  single `source=claude`/`codex`), whoever does the work. This is a required
  verification, not a required subagent — a one-line fix stays Tier 1 as long as
  the parity check runs. Source-parity regressions (e.g. the list-vs-single
  `source` crash) are the project's recurring failure mode.
- **Pure frontend palette/CSS or copy tweaks → Tier 1.** Verify by loading the
  dashboard, not by unit tests (the vanilla JS is untested by design).

## Role and skill map

| Role (agent) | Writes product code? | Return budget | Skills |
| --- | --- | --- | --- |
| orchestrator (main) | yes (Tier 0/1) | n/a | `verify`, `run`, `code-review` |
| `technical-planner` | no (plan only) | ≤10 lines | `claude-api` when LLM-adjacent; else none |
| `step-coder` | yes (sole writer) | ≤15 lines | `verify` |
| `step-intent-reviewer` | no (read-only) | ≤12 lines | `code-review` |
| `adversarial-tester` | no (read-only) | ≤12 lines | `run`, `verify` |

## Loop (a priority gate, not a fixed pipeline)

Steps are **conditional** — take the earliest EXIT that fully covers the work.
Only fall through to the heavier path when the lighter one genuinely can't.

```text
For each request or next feature:
    PREFLIGHT  derive scope; flag secrets, destructive/DB-migration, or
               unresolved product choices. Block with an exact ask.

    GATE       pick the cheapest sufficient tier and record why in one line.

    ── Tier 0 ──▶ answer with evidence.                               ► EXIT
    ── Tier 1 ──▶ main orchestrator writes the change AND runs
                  `python scripts/full_check.py` itself; report.      ► EXIT
                  (No subagent. No sentinel. This is the token-saver.)

    ── Tier 2/3 ── (only when a subagent buys confidence) ──
       plan?   spawn technical-planner ONLY if multi-step/ambiguous;
               else the orchestrator writes the one-step step_plan.md.
       code    one step-coder implements one step, then writes
               .agent/coder_done (scope).
       check   the SubagentStop hook runs the harness OUTSIDE model
               context; compact reports → .agent/, raw → .agent/raw/.
               (Hook untrusted/unavailable? orchestrator runs the harness
                itself — same retry cap. See "degraded mode" below.)
       verify  choose by risk, and run the chosen roles IN PARALLEL:
                 - reviewer   only if intent could diverge from tests
                 - tester     only for a live/user-facing/security surface
                 - neither    for mechanical, well-tested backend work
       repeat next step (Tier 3) within the retry caps.              ► EXIT

    COMPLETE   archive detail in docs/features/<feature>.md; update one index
               line in docs/feature_log.md. Stop at a feature gate and ask the
               human to review http://127.0.0.1:8080.
```

**Degraded mode (no trusted hook):** the loop still works serially — after the
coder returns, the orchestrator runs `python scripts/full_check.py` itself, reads
only the compact `.agent/` artifacts, and enforces the same caps. The hook is an
optimization (checks outside model context), not a requirement.

## Hard retry caps

- Check repair: at most **two** continuations per step.
- Intent repair: at most **one** coder revision.
- Adversarial sweep repair: **one** focused round.

A cap hit stops the loop, records `docs/blockers.md`, and asks the human.

## Stop conditions

1. Required credential, unavailable input, destructive/DB-migration decision, or
   unresolved product choice.
2. Boundary conflict with `docs/BOUNDARIES.md`.
3. Any retry cap exhausted.
4. Feature review gate reached.
5. Session ends mid-feature: index line becomes `IN PROGRESS (step k/n)`.

## Definition of done

- `python scripts/full_check.py` is green (or scope skips are legitimate).
- Required intent review approved.
- No `docs/BOUNDARIES.md` invariant crossed.
- New behavior has a real assertion in `tests/`.
- Handoff states agents used, skills used, checks, and remaining risk.

## Adaptations from LoopKit defaults (recorded per ROLES.md)

- **No E2E suite / no `adversarial-tester` browser flows by default.** The
  frontend is untested vanilla JS; the tester probes the running server's
  `/api/*` surface instead. Collapse tester into the reviewer for backend-only
  features.
- **`SPEC_FILE` is `README.md` + `docs/`,** not a single formal spec — this is a
  working codebase, not a spec-first build.

## Human kickoff

> Read `docs/agent_loop_rules.md`, `docs/feature_log.md`, and `docs/blockers.md`.
> Continue from the next incomplete item using the smallest safe execution tier.
> Stop only at a declared review gate or blocker.
