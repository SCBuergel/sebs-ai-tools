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
`devqueue` turns coordinator verdicts into events and prints the dev queue,
`lastmsg` prints an agent's last message, `dash.py` and `dashweb.py` are the
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

The limits are CPU and the tests, not memory: idle agents cost nothing, but
every dev and every commit-reviewer runs the test suite, and only
`slots/count` runs go at once. Reasonable defaults: 6 to 10 reviewers (one
per area the task splits into), devs = CPUs / 2 rounded up to at most 8,
test slots = CPUs / 3, at least 1. Check with `nproc` and `free -h`. More devs
than slots only queue on `tlock`.

Split the task into areas by trust boundary or subsystem, so that two
reviewers do not read the same files for the same question. Name each area
with one sentence saying what it covers and what question to ask of it.

## 3. Set up

Pick the swarm folder outside the repository and outside anything cleaned
at reboot, for example `~/.review-swarm/<repo name>`. Then, from the
repository:

```sh
<skill base directory>/tools/swarm-init --repo . --swarm $SW \
    --reviewers N --devs M --slots S --title "<repo> review swarm"
```

It copies the tools, writes `swarm.json`, `PROTOCOL.md` and `severity.md`,
creates branch `swarm-review` at HEAD and checks it out in the repository (the
integration tree), the detached worktree `$SW/base` that reviewers read, and
one worktree per dev on `swarm-dev<n>`. Run it a second time and it completes
what is missing and keeps what exists.

Then edit `$SW/PROTOCOL.md`: fill in the Task section (what the swarm reviews
for, and what a reviewer needs to know to judge a finding) and the Accepted
tradeoffs section (documented owner decisions reviewers must not report
again; write "none recorded" otherwise). Adapt `$SW/severity.md` to the
project's trust boundaries if the default scale does not fit. Tell the user
how to watch:

```sh
python3 $SW/dash.py            # terminal
python3 $SW/dashweb.py         # then open http://127.0.0.1:8765/
```

## 4. Start the agents

Fill each brief from `roles/` by replacing its `{{...}}` placeholders
(`{{SW}}`, `{{REPO}}`, `{{SOURCE}}`, `{{N}}`, `{{N_DEV}}`, `{{AREA}}`,
`{{FIRST_ID}}`, `{{TRAILER}}`: the commit trailer lines this session is told
to add, as " and end with these lines:" plus the lines, or nothing) and start
each agent with the Agent tool, `subagent_type` general-purpose, in the
background. Record every agent's id in `$SW/ids.txt` as `<name> <id>`; a
later session needs them to resume the agents.

Start the reviewers and the coordinator first; start a dev when the first
finding is validated. Start all devs eventually even when fewer findings are
queued: idle ones cost nothing. The `model` parameter picks the model per
agent; a dev's commit-reviewer inherits the dev's.

## 5. Dispatch

Run this loop until every finding is agreed, rejected or unresolved. Record
each transition with `$SW/ev dispatcher <kind> <id> "<msg>"`, kinds
`validating`, `valid <severity>` (written by `devqueue`), `invalid`,
`duplicate`, `dev` (handed to a dev), `coord` (fix being checked), `agreed`,
`unresolved`, and `note` for free-text narration the user reads on the
dashboards. Never judge a finding yourself: forward, record, and resume.

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
6. The coordinator replies `CONFIRMED`: log `agreed`, give the dev its next
   finding. `REVISE <id> round k`: forward to the dev as
   `REVISE <id> round k` with the numbered requests, log `dev`.
   `UNRESOLVED`: log `unresolved`, tell the user, and send the dev
   `REVERT <id>` if the coordinator asked for it.
7. A reviewer reports `done`: nothing to do. When all reviewers are done,
   the queue is empty and every dev is idle, go to section 6.

A dev must not fast-forward `swarm-review` before its post-rebase test run
has passed; remind it if the log shows `merge` before the tests ended. If a
dev's message is lost, `$SW/lastmsg <agent id>` prints its last reply.

## 6. Finish

1. In the repository, still on `swarm-review`, run the review-loop skill on
   the range `<source branch>..HEAD` with one line saying this is the
   combined range of the swarm's fixes, each already reviewed on its own,
   that tests run only through `$SW/tlock`, and that the push happens in the
   next step rather than in the loop. Fix its findings through a dev, not by
   hand.
2. On APPROVED, follow the repository's push rules: switch the repository
   back to the source branch, `git merge --ff-only swarm-review`, and push if
   the user's rules allow it, otherwise ask.
3. Log a final `note`, then run `swarm-clean --swarm $SW --repo .` to
   remove the worktrees and dev branches. The log, findings and verdicts stay
   in `$SW` for the record.
4. Report: what was pushed, every finding with its outcome, the optional
   items reviewers left, and what the local tests could not verify.

## Taking over a swarm

Another session can take over: read `$SW/PROTOCOL.md`, `$SW/ids.txt` and the
tail of `$SW/events.jsonl`, then send the running agents a short note that
this session is now the dispatcher. Agents started by a session that has
ended cannot be resumed; start replacements with the same briefs and note
the replacement in `ids.txt`.
