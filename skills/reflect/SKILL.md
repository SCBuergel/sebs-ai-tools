---
name: reflect
description: Retrospective over agent sessions. Mine one session, or many across a project or time window, for the moments the agent struggled, then propose fixes to the environment, skills, and workflows it used (navigation pointers, deterministic checks, skill or AGENTS.md edits, process changes), most-severe-first. Improves the environment, never the work itself, and proposes only. Use when the user asks for a retrospective or runs /reflect.
---

The user asked for a **retrospective**. You suggest improvements to the agent's **setup**: its environment, and the skills, processes, and workflows it used, so future runs go better. You change the setup, not the work itself: take the mistake that shipped, the fact that took twenty tool calls to find, the convention a review missed, the skill step that read ambiguously, and ask what in the project, the steering, or the workflow let it happen. The session may be any kind of work (code, research, writing, ops); the engine is the same. You propose. Nothing changes until the user picks a candidate.

Two modes run on one engine: a **single** session (default), or **cross-session** over a scope the user names. Deterministic scoring ranks the friction; you read only the flagged moments; every candidate traces to a logged line or it is dropped.

## Steps

1. **Proposals are pointers.** Any file you later propose (an `AGENTS.md` line, a skill edit, a check) should state what it is and when it fires, stay short, and push detail behind the pointer; prefer a check that can fail over a sentence. For a new skill or steering file, the in-repo `models:extension-author` skill (`models/models/skills/extension-author/references/authoring.md`) is the fuller guide.

2. **Fix mode and scope.**
   - Single (default): the current session, or a transcript the user names. Session logs live in `~/.claude/projects/<cwd-slug>/<uuid>.jsonl` (the slug is the working directory with `/` rewritten to `-`).
   - Cross-session: the user names a project directory, an explicit list, or a time window. If the scope is cross-session but **no window is given, ask the user for one** before scanning. Do not guess the window.

3. **Score friction deterministically.** Run `scan-sessions.py` over the scoped `.jsonl` files (see [`references/friction-signals.md`](references/friction-signals.md) for what it measures and how to read its output). It ranks sessions by a friction score and, within each, emits the exact jsonl line of every friction moment, so the next step reads flagged ranges, not whole transcripts.

4. **Extract candidates from the flagged moments only.**
   - Single session, or a handful: read the flagged lines yourself (`Read` with the line offset, or `sed -n`).
   - Cross-session over many sessions: dispatch one **cheap subagent** (any model) per top-K session to extract candidate findings in the schema from `friction-signals.md`, so their reading stays out of your context. Then cluster the returned findings by category and by recurrence across sessions.

5. **Classify and route each candidate** against the table below. Classify the violation before you choose a home: a **mechanical** one (a fixed pattern a check can match: a banned API, a file-location rule, a schema or format requirement) gets a check that can fail, never a sentence. Reserve prose for genuine **judgement calls**.

6. **Order by severity and present.** In cross-session mode, **recurrence is severity**: a struggle across five sessions outranks a loud one-off. Propose only. Change nothing until the user picks.

## Routing: where each fix lands

| What the log shows                                | Home for the fix                                                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Long hunt for a file or fact                      | A **navigation pointer** from a file already loaded (an `AGENTS.md` line, or a skill)                                                                                                                                                                                                                                                           |
| A mistake a check could have caught               | A **deterministic check** the domain supports: a linter, type, or test for code; a schema or data validator; a format, link, or doc check; a pre-commit hook or CI job. Read the project's own check command first, so an existing-but-unwired check is the finding, not a reinvention. A project with no guardrail at all is itself a finding. |
| A judgement-call rule a review missed             | The **relevant skill** first; then the nearest `AGENTS.md`; a review step's own standards last (for code, the `code-review` standards axis). Never a bare line in `AGENTS.md`/`CLAUDE.md`, which loads every session whether it fires or not.                                                                                                   |
| A skill or workflow step that misled the agent    | Fix that **skill's step** (the ambiguous instruction, the missing branch), or the **process** it followed (a wrong step order, a missing gate, work run sequentially that should fan out).                                                                                                                                                      |
| `AGENTS.md`/`CLAUDE.md` bloat, or steering no-ops | Move the steering into a skill or a check; delete lines that change nothing versus the agent's default.                                                                                                                                                                                                                                         |
| An expensive tool call for what it returned       | Streamline the tool or MCP, or replace it.                                                                                                                                                                                                                                                                                                      |
| Information the agent could not reach             | Widen access: tee a running process's log to a file, give read-only access to a service or data source.                                                                                                                                                                                                                                         |

The leading idea: a new rule goes where it is read when it is needed, not into always-loaded steering that every session pays for. The working agent carries the most context pressure (it explores, acts, recovers); a separate review or check step has room to apply a rule, so put it there. For code that review step is the reviewer reading a diff.

## Guardrails

- **Anchor or drop.** Every candidate names a specific session and jsonl line. One you cannot trace to a logged moment is generic advice; discard it. This is the failure mode of the genre: once the work is done the agent forgets the middle of the session and invents advice to fill the categories. The scan exists to stop that.
- **The check beats the sentence.** Default to building the deterministic check over writing a rule. A check fails; a sentence in a standards file cannot.
- **These are the only homes:** a skill or workflow step, an `AGENTS.md` pointer, a deterministic check, a tool or MCP change, a widening of access. Memory is out of scope. Write a memory file only if the user asks for one this run.
- **Propose only.** No edits, no hooks installed, no commits. The user picks what becomes permanent.

## The three roles

Scoring is deterministic (`scan-sessions.py`, stdlib Python, no model): the friction signals are simple to detect, so the script does the bulk of the work cheaply. A cheap subagent (any model, not a specific one) per session is the **secondary line of defence**: it reads the flagged moments the script could not classify, and every false positive or miss it surfaces is a cue to sharpen the script's signals, so the deterministic layer keeps getting stronger. Synthesis, routing, and severity are yours. Keep the division: the script ranks and anchors, the model pass extracts and feeds back into the script, you decide.
