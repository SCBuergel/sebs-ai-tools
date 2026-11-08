# Friction signals and extraction schema

Reference for step 3 (scoring) and step 4 (extraction) of `models:reflect`.

## What the scan measures

`scan-sessions.py` reads each session jsonl and scores friction from signals that
survive schema drift, since Claude Code does not record a reliable `is_error`
field across versions. Weights are defined once in the script (`WEIGHTS`); this
file names what each signal means, not its number.

| Signal      | What it catches                                                                                                                                                                                                                                                       | Why it means friction                                                                                                    |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| error       | a failure marker at a line start in a tool result (`Exit code N`, `error:`, `fatal:`, `Traceback`, `hook error:`, `command not found`, `no such file`), or an error word in a short result (600 chars or fewer) of a command that does not print file or diff content | A tool call came back wrong and the agent had to recover. A word inside a long diff or file dump is data and is skipped. |
| correction  | correction lexicon in a human turn (`no`, `actually`, `revert`, `that's wrong`, `instead`, ...)                                                                                                                                                                       | The user steered the agent off a path it had taken.                                                                      |
| read_repeat | the same target reached more than twice (a file read or edited, a `Grep`/`Glob` pattern, a `WebFetch` URL, a `WebSearch` query)                                                                                                                                       | Navigation cost: the agent kept returning to find or re-anchor on one thing.                                             |
| cmd_repeat  | an identical bash command run more than once                                                                                                                                                                                                                          | Thrash: a command retried against a moving target.                                                                       |
| tool_volume | log of total tool calls                                                                                                                                                                                                                                               | A weak size prior; long tool runs correlate with harder sessions.                                                        |

The weak prior only breaks ties. Errors and corrections carry the real weight,
because each points at a concrete moment you can read and route.

## Reading the output

`--json` emits, per session, a `score`, the `signals` counts, and `moments`:
each moment has the **jsonl line** (`line`), a `kind`, and an `excerpt`. Open a
moment with `Read` at that line offset, or `sed -n '<line>p' <session>`. A
revisit or repeated-command anchor carries the line of its first occurrence, so
every moment points at a real line.

Read flagged moments only. Reading whole transcripts is the cost the scan exists
to remove.

## Extraction schema

Both the single-session self-read and the fast-model subagent pass emit findings
in this shape, one per candidate:

```
- category: navigation | check | standard | steering-bloat | tool-economy | info-access
- moment: <session file>:<line>        # the anchor; tool-use-id too if present
- what_happened: one sentence, drawn from the logged moment
- proposed_fix: the environment change that prevents a repeat
- home: skill:<name> | AGENTS.md:<path> | check:<pre-commit|CI|linter-rule> | tool:<name> | access
- kind: mechanical | judgement          # mechanical defaults to a check, not prose
```

### Extraction subagent brief (cross-session)

Give each subagent (any cheap model) one session and its flagged moment lines.
Instruct it to read only those lines, emit findings in the schema above, and
invent nothing that lacks a `moment` line. It extracts; it does not route or
rank. You cluster the returned findings by `category` and by how many sessions
share one, then apply the routing table and severity order in `SKILL.md`. A
flagged moment that turns out to be a false positive, or a struggle the signals
missed, is a cue to sharpen `scan-sessions.py` so the deterministic pass catches
it next time.
