# Review swarm protocol

SW={{SW}}
Repository: {{REPO}}, reviewed from branch {{SOURCE}}.

## Task

<!-- The supervisor fills this in before starting agents: what the swarm reviews for (for example
"security of a high-value setup", "correctness of the migration layer", "docs against code"),
and anything a reviewer must know to judge a finding. -->

## How work moves: the board

Nobody relays messages. Every finding is a card on the board, `$SW/board`, moving through fixed states:

  reported → validating → valid → dev → queued → rev → agreed

with the exits invalid, duplicate and unresolved, and revise: back with the dev that fixed it. Each
agent pulls work whose state matches its role with `$SW/board next <name> <role> --wait 540`, which
prints `WORK ...` with the files to read, or `NONE ...` after waiting; on NONE, run it again unless it
says `stop` or `retire`. The agent writes its result back with the board command its brief names. Every
change is logged to `$SW/events.jsonl`, which the dashboards show. `$SW/board show [<id>]` prints the
board or one card's history.

A claim is a lease. It stays yours while you show signs of life: an `ev` event, a test run, your
commit-reviewer's events, or any `board` call. After 15 minutes without one, the board gives the card
to someone else, and your next board command about it is refused: drop it and run `next`.

## Roles
- {{N_REV}} reviewers (R1..R{{N_REV}}): each covers one area, read-only, reports findings, and later
  confirms or refuses the merged fixes of its area (`verify`).
- Validators (V1, V2, ...): read-only and short-lived. Each judges findings one at a time against the
  code, this protocol, `$SW/rules.md` and `$SW/severity.md`, and rates them. A validator also verifies a
  merged fix whose reviewer is gone.
- {{N_DEV}} devs (dev1..dev{{N_DEV}}): the only agents that edit files or commit. Each fixes one finding at
  a time in its own worktree and gets the change approved by its own commit-reviewer (the review-loop
  skill), then submits it to the merge queue.
- The merge queue (`board mergeq`, agent name merge-queue): rebases each approved change onto
  `swarm-review`, runs the full test command on that exact commit, and fast-forwards `swarm-review`.
  A conflict, a failed test or a rebase that changed the code sends the card back to the dev (revise).
- The supervisor (the session that started the swarm): owner decisions, the test infrastructure,
  starting agents and replacing dead ones, and the final whole-branch review. It does not judge findings
  and does not pass messages between agents.

Two gates approve a fix: the dev's commit-reviewer, before it enters the queue, and the reviewer who
reported the finding (or a validator in its place), after it is merged. Three refusals in a row make the
finding unresolved; the supervisor tells the user.

## Trees
- $SW/base: a detached worktree of {{SOURCE}} as it was when the swarm started. First-pass reviewers read
  this. A later pass of an area reads its own detached worktree $SW/base-R<n>-p<pass>, made from
  `swarm-review` when the pass starts. Validators judge a finding against the tree it names. Nobody edits
  these trees.
- $SW/dev1..$SW/dev{{N_DEV}}: one worktree per dev, on branch `swarm-dev1`..`swarm-dev{{N_DEV}}`. A dev edits,
  tests and commits only in its own worktree.
- $SW/merge: the merge queue's worktree. Nobody else touches it.
- {{REPO}} on branch `swarm-review`: the integration tree. Read it with `git log`, `git diff` and
  `git show`. Only the merge queue changes it; nobody edits, switches branches, pushes or resets here.

## Live status (everyone)
Report what you are doing with
  $SW/ev <YOUR_NAME> status <FINDING_ID or -> "<short message>"
at the start, at least every 10 minutes of work, and when you finish. Keep messages under 100
characters, in plain words. Devs also record their review-loop step whenever it changes:
  $SW/ev devN phase <FINDING_ID> <step>
with step one of implement, test, review-1, fix-1, review-2, fix-2, review-3, fix-3, review-4, fix-4,
approved, queued, blocked ("review-k": round k is with the dev's commit-reviewer). When you end for good,
log `$SW/ev <YOUR_NAME> stopped - "<why>"`.

## Findings
A reviewer writes each finding to $SW/queue/<id>.json (id = its name and a running number, R3-2) with the
keys id, reviewer, title, tree (the worktree the reviewer read), severity, file, line, description,
failure_scenario, suggested_fix, then runs `$SW/board report R3 R3-2`. A validator's verdict goes to
$SW/val/<id>.txt. A design finding that replaces several (root-cause escalation) is named
`<area>-D<n>`, e.g. R4-D1.

## Severity
Use the scale in $SW/severity.md (Critical/High/Medium/Low/Info). Cosmetic issues (wording,
formatting, nits) are not findings: don't report them, and reject them.

## Rules and accepted tradeoffs
$SW/rules.md lists the owner's decisions and the arguments rejected before. Read it before reporting or
judging; it changes during the run.

## Generated files
Never edit derived totals by hand, such as test counts in the docs. {{REGEN}}

## Resource limits
- Reviewers and validators run no test suites. Read and grep only.
- Every test run goes through `$SW/tlock --as <your name> <test command>` (a dev's commit-reviewer uses
  `devN-reviewer`): it waits for a free test slot (there are {{SLOTS}} at the start; the number is in
  slots/count and may change), runs the command under a memory cap, and records the holder. One command
  per run; a run may wait for another agent's. Never start a test run any other way.
- Full test command (test_cmd in swarm.json): {{TEST_CMD}}
- A commit-reviewer reruns the test suite itself, but repeats a slow extra check (for example a
  mutation run) only for a check changed in the range; the dev's commit messages list the others.
- Never run commands that change the machine or anything outside the worktrees: no provisioning,
  deployment, deletion or hardware steps, whatever the repository's guide says about them.
