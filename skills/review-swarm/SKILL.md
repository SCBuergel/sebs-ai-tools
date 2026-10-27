---
name: review-swarm
description: Run a review swarm on a repository - many read-only reviewer agents each audit one area, a coordinator validates and rates their findings, and several dev agents fix them in parallel in their own git worktrees, each fix review-looped by the dev's own commit-reviewer, with a live terminal and web dashboard. Use when the user asks for a review swarm, a parallel or multi-agent review of a whole repository or of a large change, or to scale a review across many agents. Needs the review-loop and commit-reviewer plugins.
---

# Review swarm

This skill makes the current session the **dispatcher** of a swarm of agents
that review a repository and fix what they find. The dispatcher starts the
agents, passes messages between them, and records every state change in a
shared log; it never judges a finding itself. All state lives in files in a
swarm folder, so the user can watch two dashboards and another session can
take over if this one ends.

Roles, each a subagent started with the Agent tool from the briefs under
`roles/` of this skill's base directory:

- **Reviewers** R1..Rn: read-only, one area each, write findings to
  `queue/<id>.json` and announce them.
- **Coordinator**: read-only, validates each finding against the code and
  rates it (`val/<id>.txt`), later checks each fix (`val/<id>.check.txt`).
- **Devs** dev1..devm: the only agents that edit. Each fixes one finding at a
  time in its own worktree, runs the review-loop skill on it (which starts a
  commit-reviewer subagent of the dev), and on APPROVED fast-forwards the
  integration branch `swarm-review`. Nobody pushes until the end.
- **Dash** (optional): maintains the dashboards on the user's requests.

The tools under `tools/` are copied into the swarm folder (`$SW`): `ev`
appends an event, `tlock` runs a test command in one of the shared test slots,
`ffmerge` is how a dev fast-forwards `swarm-review` (it refuses a branch
without a passing test run on its HEAD, or changed since the approval the
dev logged), `devqueue` turns coordinator verdicts into
events and prints the dev queue, `lastmsg` prints an agent's last message, `dash.py` and `dashweb.py` are the
dashboards, `swarm-init` and `swarm-clean` set up and tear down.

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

## 2. Size the swarm

The limit is the test suite: idle agents cost almost nothing, but every dev
and every commit-reviewer runs the tests. A test slot is a permit to run the
tests: with S slots, at most S test runs go at once across the whole swarm,
and any further run waits in `tlock` until one ends. Reasonable defaults: 6 to
10 reviewers (one per area the task splits into), devs = CPUs - 1 (at least
2, at most 8), test slots = CPUs / 2 rounded up (what `swarm-init` picks
when `--slots` is left out). Each test run is capped at `SWARM_TEST_MEM` (2G),
so pass a lower `--slots` when slots × 2 GB plus 4 GB does not fit the
machine's memory. Check with `nproc` and `free -h`. More devs than slots is
fine: the devs spend most of their time reading, editing and waiting on their
reviewer, not testing.

The slot count can be changed while the swarm runs: `echo 4 > $SW/slots/count`
applies to every test run that is waiting for a slot or starts afterwards. Raise it when the
dashboard's load stays below the CPU count while slots are all busy; lower it
when load goes red or memory amber.

Split the task into areas by trust boundary or subsystem, so that two
reviewers do not read the same files for the same question. Name each area
with one sentence saying what it covers and what question to ask of it.

## 3. Set up

Pick the swarm folder outside the repository and outside anything cleaned
at reboot, for example `~/.review-swarm/<repo name>`. Then, from the
repository:

```sh
<skill base directory>/tools/swarm-init --repo . --swarm $SW \
    --reviewers N --devs M --slots S --test-cmd "<full test command>" \
    --title "<repo> review swarm"
```

Pass the repository's full test command as `--test-cmd "<command>"`, exactly
as the devs will run it after `tlock --as devN`: `ffmerge` then requires a
passing run of that command on the HEAD it merges; without it any passing
tlock run counts.

By default each area is reviewed again and again by a fresh reviewer until 2
passes in a row find nothing worth fixing (see "Repeated passes" below). Pass
`--one-pass` when the user asks for a quicker check with one reviewer per
area, or `--until-clean K` for a different number of clean passes.

It copies the tools, writes `swarm.json`, `PROTOCOL.md` and `severity.md`,
creates branch `swarm-review` at HEAD and checks it out in the repository (the
integration tree), the detached worktree `$SW/base` that reviewers read, and
one worktree per dev on `swarm-dev1`, `swarm-dev2`, and so on. Run it a second time and it completes
what is missing and keeps what exists.

Then edit `$SW/PROTOCOL.md`: fill in the Task section (what the swarm reviews
for, and what a reviewer needs to know to judge a finding) and the Accepted
tradeoffs section (documented owner decisions reviewers must not report
again; write "none recorded" otherwise). Adapt `$SW/severity.md` to the
project's trust boundaries if the default scale does not fit.

## 4. Start the agents

Before the first agent, log the stage, `$SW/ev dispatcher stage - "setup <what
is being set up>"`, then start the web dashboard and post its link, so the user
can watch the swarm from its first event:

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

Fill each brief from `roles/` by replacing its `{{...}}` placeholders
(`{{SW}}`, `{{REPO}}`, `{{SOURCE}}`, `{{N}}`, `{{N_DEV}}`, `{{AREA}}`,
`{{FIRST_ID}}`, `{{TREE}}`: the tree the reviewer reads, `$SW/base` unless a
later pass gives it its own, `{{FIRST_N}}`: the number of its first finding,
1 unless an earlier reviewer of the area already reported some,
`{{TRAILER}}`: the commit trailer lines this session is told
to add, as " and end with these lines:" plus the lines, or nothing) and start
each agent with the Agent tool, `subagent_type` general-purpose, in the
background. Before the first reviewer, write each reviewer's area sentence,
the one that fills its `{{AREA}}`, to `$SW/areas.json` as
`{"R1": "<area>", "R2": "<area>", ...}`; the web dashboard shows it when the
user opens a reviewer. Record every agent's id in `$SW/ids.txt` as `<name> <id>`; a
later session needs them to resume the agents.

Start the reviewers and the coordinator first; start a dev when the first
finding is validated. Start all devs eventually even when fewer findings are
queued: idle ones cost nothing. The `model` parameter picks the model per
agent; a dev's commit-reviewer inherits the dev's.

## 5. Dispatch

When the first reviewer starts, log `$SW/ev dispatcher stage - "reviewing <N>
areas"`. Run this loop until every area is finished (see
"Repeated passes") and every finding is agreed, rejected or unresolved. Record each transition with `$SW/ev dispatcher <kind> <id>
"<msg>"`, kinds `validating`, `valid <severity>` (written by `devqueue`),
`invalid`, `duplicate`, `dev` (handed to a dev), `coord` (fix being checked
by the coordinator), `rev` (by the reporting reviewer), `agreed`,
`unresolved`, and, with id `-`, `note` for free-text narration the user reads
on the dashboards, `stage` for the stage of the whole swarm and `pass` for
repeated passes. Never judge a finding yourself: forward, record, and resume.

The dashboards show the latest `stage` as a banner with how long it has
lasted, and the last three notes under it. Log one at every change, with
the stage first and a one-line detail after it. The stages are `setup`,
`reviewing`, `paused`, `wind-down`, `final-review` (for example
`"final-review main..9e83e9b (55 commits), waiting on the final reviewer"`),
`final-fix`, `merge`, `done` and `stopped`:

- `paused` when the swarm stops for a while, for example at a usage limit;
  the dashboards then show every agent paused. Log `reviewing` (or the stage
  it was in) again when it resumes.
- `wind-down` when the user asks to finish what is in progress and start
  nothing new: log `"R5 <p> stopped"` for every pass still running, then
  start no new pass.
- `stopped` when the swarm ends without merging. After `done` or `stopped`
  the dashboards count nobody as working and close any pass still open.

The dashboards count an agent as working from its own events. Whenever you
stop an agent (TaskStop), or find that its session is gone, log `$SW/ev
<agent> stopped - "<why>"`, for example `$SW/ev dev3 stopped - "TaskStop,
swarm paused"`; a new agent of that name clears it with its first event. An
agent counted as working with no event and no test run for 20 minutes is
shown as quiet: check it with `$SW/lastmsg <agent id>`, then resume it,
replace it, or log it stopped.

1. A reviewer reports `R3-2`: send the coordinator `VALIDATE R3-2`, log
   `validating`.
2. The coordinator replies: run `$SW/devqueue`, which logs the verdict and
   prints the dev queue, highest severity first.
3. An idle dev exists and the queue is not empty: send the dev
   `IMPLEMENT <id>` (the first dev gets it in its brief as `{{FIRST_ID}}`),
   log `dev`.
4. A dev's commit-reviewer finishes: its notification may arrive here
   instead of at the dev. Forward the verdict verbatim to the dev with
   SendMessage.
5. The dev replies `DONE <id> ... commits=<hashes>`: send the coordinator
   `CHECK <id> commits=<hashes>` with the dev's report, log `coord`.
   `BLOCKED` or `DISPUTE`: send the coordinator the dev's report and let it
   decide, or hand the question to the user if the two disagree.
6. The coordinator replies `CONFIRMED`: send the reporting reviewer
   `VERIFY <id>` with the commit hashes, log `rev`, and give the dev its next
   finding meanwhile. `REVISE <id> round k`: forward to the dev as
   `REVISE <id> round k` with the numbered requests, log `dev`.
   `UNRESOLVED`: log `unresolved`, tell the user, and send the dev
   `REVERT <id>` if the coordinator asked for it.
7. The reviewer replies `SATISFIED`: log `agreed`. `NOT SATISFIED`: send its
   reasons to the coordinator as part of a new `CHECK <id>`, log `coord`; the
   coordinator decides whether they become a REVISE. A reviewer that no
   longer exists (its session ended) is skipped: log `agreed` on CONFIRMED.
8. A reviewer reports `done`: nothing to do until its pass is closed (see
   "Repeated passes"). When every area is finished, the queue is empty and
   every dev is idle, go to section 6.

A dev fast-forwards `swarm-review` only through `$SW/ffmerge`, which refuses
unless the last run of the test command on that exact HEAD passed and the
branch is the change at the approved commit the dev logged (`ev devN
approved`). Every such merge shows as a `merged` event; a DONE without one
was merged some other way: send the coordinator that with the CHECK. A dev also logs its reply as `$SW/ev
devN idle <id> "DONE ..."` before it ends its turn, so a reply that never
reached you shows up under `DEV REPORTS NOT ACTED ON` when you run
`$SW/devqueue`; `$SW/lastmsg <agent id>` prints the full reply. The
commit-reviewer's verdict for a dev may also land with you (step 4).

### Repeated passes

Unless the swarm was set up with `--one-pass` (`until_clean` in `swarm.json`
is 0, or missing in a swarm set up before 0.3.0), an area is not finished
when its reviewer is: once that reviewer is done and every finding it
reported is agreed, rejected or unresolved, a fresh reviewer reviews the area
again on the fixed tree, until `until_clean` passes in a row (2 by default)
have no finding the coordinator accepted. The fresh reviewer starts with no
memory of earlier passes, so that what the earlier reviewers learned does not
steer it: it gets the same brief, and nothing about earlier findings. The
coordinator rejects a finding it rejected before again, unless the new report
brings a new argument. Track the passes with `pass` events (id `-`; the
message starts with the reviewer), which the dashboards turn into a pass
column per area:

- `$SW/ev dispatcher pass - "R3 1 started"` when the first reviewer of the
  area starts.
- When pass p of R3 is closed: `"R3 <p> clean"` if none of its findings was
  validated, otherwise `"R3 <p> not clean"`. With `until_clean` clean passes in a row,
  log `"R3 <p> finished"` and stop; otherwise start the next pass.
- Next pass: `git -C <repo> worktree add --detach $SW/base-R3-p<p+1>
  swarm-review`, fill the reviewer brief with that tree as `{{TREE}}` and the
  next free number as `{{FIRST_N}}`, and nothing else changed, start it as a
  new agent named R3, record it in `ids.txt` as `R3-p<p+1> <id>`, and log
  `"R3 <p+1> started"`. A VERIFY goes to the reviewer of the pass that
  reported the finding while it exists.
- If the user stops an area early: `"R3 <p> stopped"`.

With `--one-pass`, an area is finished when its reviewer is done; log no
`pass` events.

## 6. Finish

1. In the repository, still on `swarm-review`, run the review-loop skill on
   the range `<source branch>..HEAD` with one line saying this is the
   combined range of the swarm's fixes, each already reviewed on its own,
   and that the push happens in the next step rather than in the loop, and
   the line "Run tests only through `$SW/tlock --as final-reviewer` (one
   command per run)". Log `stage - "final-review <range> (<n> commits)"` and,
   for the dashboards' agent list, `$SW/ev final-reviewer status - "reviewing
   <range>"`; when it answers, `$SW/ev final-reviewer done - "<verdict>"`.
   Fix its findings through a dev, not by hand (`stage - "final-fix ..."`),
   each as a new finding `F-1`, `F-2`, ... (never under the id of a finding
   it touches: `agreed`, `unresolved`, `invalid` and `duplicate` are final,
   and an event after them reopens the finding on the dashboards). For each:
   write `$SW/queue/F-<n>.json` with the keys a reviewer uses (reviewer
   `"final-reviewer"`) and `$SW/val/F-<n>.txt` with the first line
   `VALID F-<n> <severity>` followed by the review's text as the fix
   requirements, run `$SW/ev final-reviewer reported F-<n> "<title>"` and
   `$SW/devqueue`, then hand it to a dev as in section 5 (`dev`). The
   coordinator and reviewer steps are skipped: when the dev reports `DONE`,
   resume the same final reviewer on the new range, and on APPROVED log
   `agreed F-<n> "final review APPROVED"` for every open `F-<n>`.
2. On APPROVED, log `stage - "merge ..."` and follow the repository's push
   rules: switch the repository back to the source branch, `git merge
   --ff-only swarm-review`, and push if the user's rules allow it, otherwise
   ask.
3. Log `stage - "done <what was pushed>"`, then run `swarm-clean --swarm $SW
   --repo .` to remove the worktrees (including the per-pass ones) and dev
   branches. The log, findings and verdicts stay
   in `$SW` for the record. Never clean up before the final review is
   APPROVED and merged: a final finding needs a dev and its worktree. If the
   worktrees are gone anyway, run `swarm-init` again with the same options
   on `swarm-review`; it recreates the missing dev worktrees.
4. Report: what was pushed, every finding with its outcome, the optional
   items reviewers left, and what the local tests could not verify.

## Taking over a swarm

Another session can take over: read `$SW/PROTOCOL.md`, `$SW/ids.txt` and the
tail of `$SW/events.jsonl`, then send the running agents a short note that
this session is now the dispatcher. Check that the link in `$SW/web.url`
still serves the page (start the server again as in section 4 if not) and
post it to the user. Agents started by a session that has ended cannot be
resumed; start replacements with the same briefs and note the replacement in
`ids.txt`.
