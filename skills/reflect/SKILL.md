---
name: reflect
description: Retrospective over agent sessions, including long multi-agent runs with subagents, orchestration logs and messages between sessions. Mine one session, or many across a project or time window, for the moments the agents struggled, then propose fixes to the environment, skills, and workflows it used (navigation pointers, deterministic checks, skill or AGENTS.md edits, process changes), most-severe-first. Improves the environment, never the work itself, and proposes only. Use when the user asks for a retrospective or runs /reflect:reflect.
---

The user asked for a **retrospective**. You suggest improvements to the agent's **setup**: its environment, and the skills, processes, and workflows it used, so future runs go better. You change the setup, not the work itself: take the mistake that shipped, the fact that took twenty tool calls to find, the convention a review missed, the skill step that read ambiguously, and ask what in the project, the steering, or the workflow let it happen. The session may be any kind of work (code, research, writing, ops); the engine is the same. You propose. Nothing changes until the user picks a candidate.

Two modes run on one engine: a **single** run (default), or **cross-session** over a scope the user names. A **run** is one session together with every subagent it started, scored as a whole, so a swarm of agents counts as one piece of work and not as a hundred unrelated sessions. Deterministic scoring ranks the friction; you read only the flagged moments; every candidate traces to a logged line or it is dropped.

## Steps

1. **Proposals are pointers.** Any file you later propose (an `AGENTS.md` line, a skill edit, a check) should state what it is and when it fires, stay short, and push detail behind the pointer; prefer a check that can fail over a sentence. For a new skill or steering file, Tibor's `agent-extension-author` skill (plugin `agent-extension-author@kauki-skills`, from Teebor-Choka/skills) is the fuller guide, if it is installed.

2. **Fix mode and scope.**
   - Single (default): the current session, or a transcript the user names. Session logs live in `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` (the slug is the working directory with `/` rewritten to `-`), and the subagents it started in `<uuid>/subagents/`. Pass the project folder or both paths: the scan joins them into one run.
   - A session continued under a new id (after a restart) is the same work: pass all its transcripts with `--run`.
   - If the session orchestrated agents through a shared log (a work board, an event log, a queue), pass that JSONL with `--coordination`. Stalls, rejected or duplicate work, unresolved items and revision rounds live there, not in the transcripts.
   - Messages from other sessions show up as `peer` moments. To judge the other session too, add its transcripts to the scope.
   - Cross-session: the user names a project directory, an explicit list, or a time window. If the scope is cross-session but **no window is given, ask the user for one** before scanning. Do not guess the window.

3. **Score friction deterministically.** Run `scan-sessions.py` over the scoped `.jsonl` files (see [`references/friction-signals.md`](references/friction-signals.md) for what it measures and how to read its output). It ranks runs by a friction score; within each, it ranks the transcripts (the main one and each subagent, with its type, description and token cost) and emits the exact jsonl line of every friction moment, spread across the whole transcript, so the next step reads flagged ranges, not whole transcripts.

4. **Extract candidates from the flagged moments only.**
   - Single session, or a handful: read the flagged lines yourself (`Read` with the line offset, or `sed -n`).
   - Read every `steer` and `correction` moment in the main transcript. They are the user's own turns, few even in a long run, and the strongest evidence.
   - A multi-agent run, or cross-session over many sessions: dispatch one **cheap subagent** (any model) per group to extract candidate findings in the schema from `friction-signals.md`, so their reading stays out of your context. Group a run by agent type (all devs, all reviewers), plus one for the main transcript's moments and one for the run-level moments (coordination events, unfinished agents, outlier cost). Then cluster the returned findings by category and by recurrence.

5. **Classify and route each candidate** against the table below. Classify the violation before you choose a home: a **mechanical** one (a fixed pattern a check can match: a banned API, a file-location rule, a schema or format requirement) gets a check that can fail, never a sentence. Reserve prose for genuine **judgement calls**.

6. **Order by severity and present.** **Recurrence is severity**: a struggle across five sessions, or across five subagents of one role in a run, outranks a loud one-off. One role failing the same way repeatedly points at that role's brief. Propose only. Change nothing until the user picks.

## Routing: where each fix lands

| What the log shows                                | Home for the fix                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Long hunt for a file or fact                      | A **navigation pointer** from a file already loaded (an `AGENTS.md` line, or a skill)                                                                                                                                                                                                                                                           |
| A mistake a check could have caught               | A **deterministic check** the domain supports: a linter, type, or test for code; a schema or data validator; a format, link, or doc check; a pre-commit hook or CI job. Read the project's own check command first, so an existing-but-unwired check is the finding, not a reinvention. A project with no guardrail at all is itself a finding. |
| A judgement-call rule a review missed             | The **relevant skill** first; then the nearest `AGENTS.md`; a review step's own standards last (for code, the `code-review` standards axis). Never a bare line in `AGENTS.md`/`CLAUDE.md`, which loads every session whether it fires or not.                                                                                                   |
| A skill or workflow step that misled the agent    | Fix that **skill's step** (the ambiguous instruction, the missing branch), or the **process** it followed (a wrong step order, a missing gate, work run sequentially that should fan out).                                                                                                                                                      |
| `AGENTS.md`/`CLAUDE.md` bloat, or steering no-ops | Move the steering into a skill or a check; delete lines that change nothing versus the agent's default.                                                                                                                                                                                                                                         |
| An expensive tool call for what it returned       | Streamline the tool or MCP, or replace it.                                                                                                                                                                                                                                                                                                      |
| A coordination failure between agents             | The **orchestrating skill**: the step or role brief that let it happen (a stalled agent nobody noticed, duplicate or rejected work, a rework loop, a lost message), or its tooling (board, queue, lock, dashboard). A check there beats a sentence in a brief.                                                                                    |
| Agents that cost more than their work returned    | The orchestrating skill's sizing or delegation step (how many agents, which model, when to spawn at all), or the brief that sent an agent searching.                                                                                                                                                                                             |
| Information the agent could not reach             | Widen access: tee a running process's log to a file, give read-only access to a service or data source.                                                                                                                                                                                                                                         |

The leading idea: a new rule goes where it is read when it is needed, not into always-loaded steering that every session pays for. The working agent carries the most context pressure (it explores, acts, recovers); a separate review or check step has room to apply a rule, so put it there. For code that review step is the reviewer reading a diff.

## Guardrails

- **Anchor or drop.** Every candidate names a specific session and jsonl line. One you cannot trace to a logged moment is generic advice; discard it. This is the failure mode of the genre: once the work is done the agent forgets the middle of the session and invents advice to fill the categories. The scan exists to stop that.
- **The check beats the sentence.** Default to building the deterministic check over writing a rule. A check fails; a sentence in a standards file cannot.
- **These are the only homes:** a skill or workflow step, an `AGENTS.md` pointer, a deterministic check, a tool or MCP change, a widening of access. Memory is out of scope. Write a memory file only if the user asks for one this run.
- **Propose only.** No edits, no hooks installed, no commits. The user picks what becomes permanent.
- **Know who spoke.** In a subagent's transcript the "user" is the agent that started it; its follow-ups are `rework`, never the user's corrections. Messages from other sessions are `peer`. Attribute a finding to the user only from a `steer` or `correction` in a main transcript.

## The three roles

Scoring is deterministic (`scan-sessions.py`, stdlib Python, no model): the friction signals are simple to detect, so the script does the bulk of the work cheaply. A cheap subagent (any model, not a specific one) per session is the **secondary line of defence**: it reads the flagged moments the script could not classify, and every false positive or miss it surfaces is a cue to sharpen the script's signals, so the deterministic layer keeps getting stronger. Synthesis, routing, and severity are yours. Keep the division: the script ranks and anchors, the model pass extracts and feeds back into the script, you decide.
