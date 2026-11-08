# Friction signals and extraction schema

Reference for step 3 (scoring) and step 4 (extraction) of the `reflect` skill.

## What the scan measures

`scan-sessions.py` reads each session jsonl and scores friction from signals that
survive schema drift, since Claude Code does not record a reliable `is_error`
field across versions. Weights are defined once in the script (`WEIGHTS`); this
file names what each signal means, not its number.

| Signal      | What it catches                                                                                                                                                                                                                                                       | Why it means friction                                                                                                    |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| error | a failure marker at a line start in a tool result (`Exit code N`, `error:`, `fatal:`, `Traceback`, `hook error:`, `command not found`, `no such file`, a lint summary such as `Found 2 errors.`, `3 problems` or `would reformat`), or an error word in a short result (600 chars or fewer) of a command that does not print file or diff content. Not an error: exit 1 from `grep`, `rg`, `diff` or `test`, or exit 2 from `ls`, as the last program of a single pipeline (after any `cd ... &&`) (nothing found, files differ, path gone). A failed test run (`pytest`, `cargo test`, `tlock`, ...) still counts but its moment carries `tag: test_run`, since the failure may be deliberate, such as a mutation check | A tool call came back wrong and the agent had to recover. A word inside a long diff or file dump is data and is skipped; so is a probe that found nothing. |
| correction  | correction lexicon in a human turn (`no`, `actually`, `revert`, `that's wrong`, `instead`, ...)                                                                                                                                                                       | The user steered the agent off a path it had taken.                                                                      |
| read_repeat | the same target reached more than twice (a file read or edited, a `Grep`/`Glob` pattern, a `WebFetch` URL, a `WebSearch` query)                                                                                                                                       | Navigation cost: the agent kept returning to find or re-anchor on one thing.                                             |
| cmd_repeat  | an identical bash command run more than once                                                                                                                                                                                                                          | Thrash: a command retried against a moving target.                                                                       |
| tool_volume | log of total tool calls, each `tool_use` id counted once per run even when the transcript logs its message again                                                                                                                                                                                                                                               | A weak size prior; long tool runs correlate with harder sessions.                                                        |
| steer       | a human turn in a main transcript, after the agent acted, without the correction lexicon                                                                                                                                                                             | Users redirect in plain sentences ("The timer should start after…") as often as with "no" or "actually".                 |
| rework | in a subagent's transcript: a plain follow-up turn from the agent that started it, or a message from another agent (a lead's `REVISE`, its reviewer's `NOT APPROVED`, a refusal or conflict) that asks for another round, judged on the message itself, not the harness's wrapper around it. A new assignment such as `IMPLEMENT R7-2` and the harness's task notifications are not rework | Another round: a revision request, a fix that was not accepted. |
| peer | a message from another session or agent (`<cross-session-message>`, `<teammate-message>`, `<agent-message>`), counted once each even when it is logged several times (queued as an attachment, then delivered), by its text; not scored | Coordination across sessions. Not the user, so never a correction. |
| agent_failure | a subagent whose task notification ends with a status other than `completed`                                                                                                                                                                                        | An agent was killed, failed or stopped, and its work had to be redone or dropped.                                        |
| unfinished  | (run level) a subagent whose `.meta.json` says it did not finish                                                                                                                                                                                                       | The agent was abandoned or still running when the session ended.                                                         |
| cost | (run level) a subagent whose tokens per round exceed twice the run's median, among the three highest. Tokens are the highest total its notifications report, which is cumulative over resumes; tool uses and duration are summed over its rounds | Spend out of line with the rest; read what it was asked and what it returned. Per round, so an agent resumed many times is not an outlier for living long. |
| coordination | (run level) an event in a `--coordination` log whose kind matches `--coordination-kinds` (default: stop, stall, quiet, invalid, duplicate, unresolved, revise, reject, block, dispute, timeout, fail, lost, conflict, retry, abandon, error, expired, refus)          | Friction between agents that no transcript shows.                                                                        |

The weak prior only breaks ties. Errors and corrections carry the real weight,
because each points at a concrete moment you can read and route.

## Reading the output

`--json` emits, per **run**, a `score`, the summed `signals` (including the
number of `subagents` and their `tokens`), run-level `moments`, the counts of
every `coordination_kinds` and negative events per agent
(`coordination_by_agent`), and `members`: one entry per transcript, highest
score first, each with its own `score`, `signals`, `moments`, its `agent` (id,
type, description, finished; `null` for a main transcript) and its `cost`
(tokens, tool uses, duration, status and `rounds`, from the parent's
notifications). With `--run`, an entry whose `uuid` an earlier transcript of
the run already holds is skipped, so a continuation that repeats its parent's
turns does not count them twice. Each
moment has the **jsonl line** (`line`), a `kind`, and an `excerpt`; run-level
moments also name their `file`. Open a moment with `Read` at that line offset,
or `sed -n '<line>p' <session>`. A revisit or repeated-command anchor carries
the line of its first occurrence, so every moment points at a real line.

A transcript reports 20 moments per 1,500 lines, up to 120 (`--moments` sets a
fixed number). The budget is filled in order: the user's corrections and
steers, then rework and failed agents, then peer messages, then errors, so a
flood of peer messages never hides the user's own turns. Each group is spread
evenly across the transcript, so a long session is not judged by its first hour. Run-level coordination moments
are spread the same way, up to 60.

Read flagged moments only. Reading whole transcripts is the cost the scan exists
to remove.

## Extraction schema

Both the single-session self-read and the fast-model subagent pass emit findings
in this shape, one per candidate:

```
- category: navigation | check | standard | steering-bloat | tool-economy | info-access | coordination | cost
- moment: <session file>:<line>        # the anchor; tool-use-id too if present
- what_happened: one sentence, drawn from the logged moment
- proposed_fix: the environment change that prevents a repeat
- home: skill:<name> | AGENTS.md:<path> | check:<pre-commit|CI|linter-rule> | tool:<name> | access
- who: user | parent-agent | peer-session | tool   # whose turn the moment is; never "user" for rework
- kind: mechanical | judgement          # mechanical defaults to a check, not prose
```

### Extraction subagent brief (cross-session)

Give each subagent (any cheap model) one session, or one group of a run's
transcripts (one agent type), and its flagged moment lines.
Instruct it to read only those lines, emit findings in the schema above, and
invent nothing that lacks a `moment` line. It extracts; it does not route or
rank. You cluster the returned findings by `category` and by how many sessions
share one, then apply the routing table and severity order in `SKILL.md`. A
flagged moment that turns out to be a false positive, or a struggle the signals
missed, is a cue to sharpen `scan-sessions.py` so the deterministic pass catches
it next time.
