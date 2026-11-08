---
name: review-swarm
description: Run a review swarm on a repository - many read-only reviewer agents each audit one area, short-lived validators judge their findings, and several dev agents fix them in parallel in their own git worktrees, each fix approved by the dev's own commit-reviewer and merged by a test-gated merge queue, all coordinating through a shared work board, with a live terminal and web dashboard. Use when the user asks for a review swarm, a parallel or multi-agent review of a whole repository or of a large change, or to scale a review across many agents. Needs the review-loop and commit-reviewer plugins.
---

# Review swarm

This skill makes the current session the **supervisor** of a swarm of agents
that review a repository and fix what they find. The agents do not message
each other or the supervisor: they coordinate through a shared **work board**.
Each finding is a card that moves through fixed states (reported → validating
→ valid → dev → queued → rev → agreed, or invalid, duplicate, unresolved, and
revise back to its dev). Each agent claims the next card whose state matches
its role and writes the result back. A claim is a lease: a claim whose owner
shows no sign of life for 15 minutes goes back on the board for another agent.
All state lives in files in a swarm folder, so the dashboards always show the
board as it is, and another session can take over if this one ends.

Roles, each a subagent started with the Agent tool from the briefs under
`roles/` of this skill's base directory:

- **Reviewers** R1..Rn: read-only, one area each. They report findings, and
  once done they stay to confirm or refuse the merged fixes of their area.
- **Validators** V1, V2, ...: read-only and short-lived. Each judges findings
  one at a time against the code and the shared rules file, rates them, merges
  findings with one root cause into a design finding, and verifies a fix whose
  reviewer is gone. A validator retires after 8 verdicts, so no context grows
  without bound; the supervisor starts fresh ones as needed.
- **Devs** dev1..devm: the only agents that edit. Each fixes one finding at a
  time in its own worktree, gets it APPROVED by its own commit-reviewer (the
  review-loop skill), and submits it to the merge queue. Nobody pushes until
  the end.
- **Merge queue**: a process, not an agent (`board mergeq`). It rebases each
  approved change onto the integration branch `swarm-review`, runs the full
  test command on that exact commit, and fast-forwards. A conflict, a failed
  test or a rebase that changed the code sends the card back to its dev; a
  conflict, or a difference, only in import lines it resolves itself, and the
  approval stands. A
  failed run is repeated once: tests that fail and then pass send it back as
  `FLAKY`, to be fixed rather than merged.
- **Dash** (optional): maintains the dashboards on the user's requests. The
  supervisor forwards each request verbatim as `DASH REQUEST: ...`; a reply
  ending in `NEEDS FROM SUPERVISOR:` asks for an event the log does not carry
  yet.

The supervisor makes the owner's decisions effective (rules file, pause,
wind-down), keeps the test infrastructure and the merge queue running,
starts agents and replaces dead ones, and runs the final whole-branch review.
It never judges a finding and never passes messages between agents.

The tools under `tools/` are copied into the swarm folder (`$SW`): `board`
is the work board and the merge queue (its header lists every command), `ev`
appends an event to the log, `tlock` runs a test command in one of the shared
test slots, `lastmsg` prints an agent's last message, `dash.py` and
`dashweb.py` are the dashboards, `swarm-init` and `swarm-clean` set up and
tear down.

## 1. Check the ground

- The `review-loop` skill and the `commit-reviewer` agent are installed (the
  devs invoke `review-loop:review-loop`, which starts
  `commit-reviewer:commit-reviewer`). If not, stop and tell the user.
- The repository has a guide for AI assistants (`AGENTS.md` or `CLAUDE.md`)
  that names its test commands; the devs and their reviewers follow it. If it
  has none, ask the user for the test command and the rules to judge by.
- The checkout is clean and on the branch the review starts from.
- `python3`, `flock` and `git worktree` are available; `systemd-run` is
  optional (without it tests run uncapped).
- The agents can run the swarm's tools without a permission prompt. A prompt
  nobody answers ends an agent's turn, and a refused one ends a review round
  without a verdict. `swarm-init` prints the allow rules a swarm needs, with
  `$SW` filled in: `Bash(<SW>/tlock:*)`, `Bash(<SW>/board:*)`,
  `Bash(<SW>/ev:*)`, `Bash(python3 <SW>/board:*)`, `Bash(git worktree add:*)`
  and `Bash(git worktree remove:*)`. Show them to the user, who can add them
  with `/permissions` or to the project's `.claude/settings.local.json`.
  Never edit the user's settings yourself.

## 2. Size the swarm

The limit is the test suite: idle agents cost almost nothing, but every dev,
every commit-reviewer and the merge queue run the tests. A test slot is a
permit to run the tests: with S slots, at most S test runs go at once across
the whole swarm, and any further run waits in `tlock` until one ends.
Reasonable defaults: 6 to 10 reviewers (one per area the task splits into), 2
or 3 validators, devs = CPUs - 1 (at least 2, at most 8), test slots = CPUs / 2
rounded up (what `swarm-init` picks when `--slots` is left out). Each test run
is capped at `SWARM_TEST_MEM` (2G), so pass a lower `--slots` when slots × 2 GB
plus 4 GB does not fit the machine's memory. Check with `nproc` and `free -h`.

The slot count can be changed while the swarm runs: `echo 4 > $SW/slots/count`
applies to every test run that is waiting for a slot or starts afterwards.
Raise it when the dashboard's load stays below the CPU count while slots are
all busy; lower it when load goes red or memory amber.

Split the task into areas by trust boundary or subsystem, so that two
reviewers do not read the same files for the same question. Name each area
with one sentence saying what it covers and what question to ask of it.

## 3. Set up

Pick the swarm folder outside the repository and outside anything cleaned
at reboot, for example `~/.review-swarm/<repo name>`. Then, from the
repository:

```sh
<skill base directory>/tools/swarm-init --repo . --swarm $SW \
    --reviewers N --devs M --validators V --slots S \
    --test-cmd "<full test command>" [--regen-cmd "<command>"] \
    [--py "<interpreter command>"] --title "<repo> review swarm"
```

`--test-cmd` is required: the merge queue runs exactly this command on every
change before it reaches `swarm-review`. If the repository keeps derived
totals in its docs, such as test counts, pass the command that regenerates
them as `--regen-cmd`: devs then never edit those totals, which removes most
rebase conflicts, and you run it once before the final review.

For a Python repository, find the interpreter that runs its tests once, with
its environment, and pass it as `--py`, for example `--py "env PYTHONPATH=src
$PWD/.venv/bin/python"`. Use an absolute path: the worktrees have no
virtualenv of their own. swarm-init writes it as the wrapper `$SW/py` and
names it in the protocol, so no agent hunts for it; write `--test-cmd` with
the wrapper's absolute path too, for example `"$SW/py -m pytest"` expanded by
your shell. If the project sorts its imports with a tool, pass it as
`--import-fix`, for example `--import-fix "ruff check --select I --fix"`: the
merge queue resolves a rebase conflict that is only in import lines itself by
keeping both sides, runs that, then the tests.

By default each area is reviewed again and again by a fresh reviewer until 2
passes in a row are clean: no valid finding at Medium or above. Low and Info
findings are still fixed, but they do not keep an area in review, and an area
gets at most 6 passes; the board logs one that hits the cap as `capped`. Pass
`--one-pass` when the user asks for a quicker check with one reviewer per
area, `--until-clean K` for a different number of clean passes,
`--clean-below SEV` for another threshold, or `--max-passes P` for another
cap.

It copies the tools, writes `swarm.json`, `PROTOCOL.md`, `severity.md` and
`rules.md`, creates branch `swarm-review` at HEAD and checks it out in the
repository (the integration tree), the detached worktree `$SW/base` that
reviewers read, `$SW/merge` for the merge queue, and one worktree per dev on
`swarm-dev1`, `swarm-dev2`, and so on. Run it a second time and it completes
what is missing and keeps what exists.

Then fill in the Task section of `$SW/PROTOCOL.md` (what the swarm reviews
for, and what a reviewer needs to know to judge a finding) and the Accepted
tradeoffs in `$SW/rules.md` (documented owner decisions; "none recorded"
otherwise). Adapt `$SW/severity.md` to the project's trust boundaries if the
default scale does not fit.

## 4. Start

Log the stage first, `$SW/ev supervisor stage - "setup <what is being set
up>"`, then start the web dashboard and post its link, so the user can watch
from the first event:

1. If `curl -s http://127.0.0.1:8765/swarm.json` returns this swarm's
   `swarm.json` (a rerun, or a server the user started), keep that server.
   Otherwise run `python3 $SW/dashweb.py <port>` with the Bash tool in the
   background, port 8765 or, if that is taken, the next free one.
2. Check with `curl -sf http://127.0.0.1:<port>/` that it serves the page.
3. Post the link to the user as its own line, `Web dashboard:
   http://127.0.0.1:<port>/`, together with the terminal dashboard command
   `python3 $SW/dash.py`. Write the link to `$SW/web.url`, so a session that
   takes over can post it again.

If the server does not start, say so with its error, give the user the
command to start it themselves, and go on: the swarm does not need it.

Start the merge queue with the Bash tool in the background: `python3
$SW/board mergeq`. It runs until stopped.

Fill each brief from `roles/` by replacing its `{{...}}` placeholders
(`{{SW}}`, `{{REPO}}`, `{{SOURCE}}`, `{{N}}`, `{{N_DEV}}`, `{{NAME}}`: a
validator's name, `{{AREA}}`, `{{TREE}}` and `{{FIRST_N}}`: the reviewer's
tree and first finding number, which `board pass` prints, `{{TRAILER}}`: the
commit trailer lines this session is told to add, as " and end with these
lines:" plus the lines, or nothing) and start each agent with the Agent tool,
`subagent_type` general-purpose, in the background. Before the first
reviewer, write each reviewer's area sentence to `$SW/areas.json` as
`{"R1": "<area>", "R2": "<area>", ...}`; the web dashboard shows it. Record
every agent's id in `$SW/ids.txt` as `<name> <id>`; a later session needs
them.

1. Reviewers: for each area run `$SW/board pass R<n> start`, which prints the
   pass's `TREE` and `FIRST_N`, then start the reviewer with them. Log
   `$SW/ev supervisor stage - "reviewing <N> areas"`.
2. Validators V1..V<v> (`swarm.json` validators).
3. Devs dev1..dev<m>, all at once: one with nothing to claim waits on the
   board at almost no cost.

The `model` parameter picks the model per agent; a dev's commit-reviewer
inherits the dev's.

## 5. Supervise

Run `$SW/board watch` with the Bash tool in the background. It exits as soon
as the board has something new for you, and prints `$SW/board tick`'s view:
counts per state, and lines to act on. Act on each, then start `board watch`
again. When a subagent's completion notification arrives, run `$SW/board
tick` yourself.

- `START reviewer R3 pass p`: the area's previous pass is closed and not yet
  clean enough. Run `$SW/board pass R3 start` and start a fresh agent named R3
  with the same brief and the printed `TREE` and `FIRST_N`, and nothing else
  changed: it starts with no memory of earlier passes, so what earlier
  reviewers learned does not steer it. Record it in `ids.txt` as `R3-p<p>
  <id>`. The board itself logs when a pass is clean, not clean or finished.
- `START validator V<k>`: findings wait and fewer validators than configured
  are alive. Start one with that name.
- `START merge queue`: changes wait and no merge queue is running. Start it
  again as in section 4; a second one refuses to start while one runs.
- `QUIET devN`: a dev with work waiting shows no sign of life. Check it with
  `$SW/lastmsg <agent id>`; resume it with SendMessage, or log `$SW/ev devN
  stopped - "<why>"` and start a replacement with the same brief. Its claims
  already went back on the board.
- `USER unresolved ...`: tell the user about each such finding once, with
  `$SW/board show <id>`.
- `FINISHED`: go to section 6.

Owner decisions go where the agents read them, not into messages:

- An accepted tradeoff, or any other ruling on what counts as a finding: add
  it to `$SW/rules.md` with the date. Validators reread it before every
  verdict.
- A ruling that changes what a validated finding's fix must do (a
  requirement withdrawn or changed): `$SW/board amend supervisor <id>
  "<the change>"`. It appends the change to `$SW/val/<id>.txt`, which the
  dev and the verifier work from, and logs it in one step.
- Pause (for example at a usage limit): `$SW/board flag paused on` and `$SW/ev
  supervisor stage - "paused <why>"`. Agents then find no work; resume with
  `flag paused off` and log the previous stage again.
- Wind-down (finish what is in progress, start nothing new): `$SW/board flag
  wind-down on`, `$SW/board pass R<n> stop` for every pass still running, and
  `stage - "wind-down"`. Devs then take only cards already theirs; validated
  findings nobody claimed are listed as left open.

The dashboards show the latest `stage` as a banner with the last three
`note` events under it (`$SW/ev supervisor note - "<text>"`). Log one at
every change. The stages are `setup`, `reviewing`, `paused`, `wind-down`,
`final-review`, `final-fix`, `merge`, `done` and `stopped` (the swarm ends
without merging). Whenever you stop an agent (TaskStop) or find its session
gone, log `$SW/ev <agent> stopped - "<why>"`.

## 6. Finish

1. Stop starting agents. Log `stage - "final-review ..."`. If `swarm.json`
   has a `regen_cmd`, run it in a dev worktree reset to `swarm-review`,
   commit the result as `[swarm] regenerate totals`, and fast-forward
   `swarm-review` to it after the test command passes in that worktree
   through `tlock --as supervisor`; the final review covers the commit.
2. In the repository, still on `swarm-review`, run the review-loop skill on
   the range from the source branch to HEAD, given as hashes
   (`<source sha>..<head sha>`), with one line saying this is the
   combined range of the swarm's fixes, each already reviewed on its own,
   and that the push happens in the next step rather than in the loop, and
   the line "Run tests only through `$SW/tlock --as final-reviewer` (one
   command per run)". Log `$SW/ev final-reviewer status - "reviewing
   <range>"`; when it answers, `$SW/ev final-reviewer done - "<verdict>"`.
3. Fix its findings through the devs, not by hand (`stage - "final-fix
   ..."`), each as a new finding `F-1`, `F-2`, ... For each: write
   `$SW/queue/F-<n>.json` with the keys a reviewer uses (reviewer
   `"final-reviewer"`) and `$SW/val/F-<n>.txt` with the first line `VALID
   F-<n> <severity>` followed by the review's text as the fix requirements,
   then run `$SW/board report final-reviewer F-<n>` and `$SW/board verdict
   supervisor F-<n>`. A dev claims it and the merge queue merges it. When
   every F card is in state rev, resume the same final reviewer on the new
   range; on APPROVED, run `$SW/board confirm supervisor F-<n>` for each.
4. On APPROVED, log `stage - "merge ..."`, stop the merge queue and `board
   watch`, and follow the repository's push rules: switch the repository back
   to the source branch, `git merge --ff-only swarm-review`, and push if the
   user's rules allow it, otherwise ask.
5. Log `stage - "done <what was pushed>"`, then run `swarm-clean --swarm $SW
   --repo .` to remove the worktrees and dev branches. The log, the board and
   the verdicts stay in `$SW` for the record. Never clean up before the final
   review is APPROVED and merged: a final finding needs a dev and its
   worktree. If the worktrees are gone anyway, run `swarm-init` again with the
   same options on `swarm-review`; it recreates the missing ones.
6. Report: what was pushed, every finding with its outcome (`$SW/board
   show`), what the wind-down left open, the optional items reviewers left,
   and what the local tests could not verify.

## Taking over a swarm

Another session can take over: read `$SW/PROTOCOL.md`, `$SW/rules.md`,
`$SW/ids.txt`, `$SW/board tick` and the tail of `$SW/events.jsonl`. Agents
keep working on the board without a supervisor; what stops is starting new
passes and validators, and replacing dead agents. Check that the link in
`$SW/web.url` still serves the page (start the server again as in section 4 if
not) and post it to the user, start the merge queue if `tick` asks for it,
and start `board watch`. Agents started by a session that has ended cannot be
resumed; start replacements with the same briefs and note them in `ids.txt`.
