# Review swarm protocol

SW={{SW}}
Repository: {{REPO}}, reviewed from branch {{SOURCE}}.

## Task

<!-- The dispatcher fills this in before starting agents: what the swarm reviews for (for example
"security of a high-value setup", "correctness of the migration layer", "docs against code"),
and anything a reviewer must know to judge a finding. -->

## Roles
- {{N_REV}} reviewers (R1..R{{N_REV}}): each covers one area, read-only, and reports findings.
- 1 coordinator: validates findings, rates them, and checks the devs' fixes. Read-only.
- {{N_DEV}} devs (dev1..dev{{N_DEV}}): the only agents that edit files or commit. Each dev works on one
  finding at a time in its own worktree and branch, and gets every change review-looped before
  reporting it: the dev runs the review-loop skill itself, which starts a commit-reviewer subagent
  on the range `swarm-review..HEAD` of its branch, fixes the findings, and resubmits to that same
  reviewer until it says APPROVED. The reviewer never pushes and neither does the dev; on APPROVED
  the dev fast-forwards `swarm-review` instead (see Trees).
- The dispatcher (the session that started the agents) passes messages between agents and records
  state transitions. It never judges a finding.
- A fix is agreed once the dev's own commit-reviewer approved it and the coordinator confirmed that
  it meets the validation.

## Trees
- $SW/base: a detached worktree of {{SOURCE}} as it was when the swarm started. First-pass reviewers
  read this. A later pass of an area reads its own detached worktree $SW/base-R<n>-p<pass>, made from
  `swarm-review` when the pass starts. The coordinator judges a finding against the tree it names.
  Nobody edits these trees.
- $SW/dev1..$SW/dev{{N_DEV}}: one worktree per dev, on branch `swarm-dev1`..`swarm-dev{{N_DEV}}`. A dev edits,
  tests and commits only in its own worktree. Before starting a finding it runs `git rebase swarm-review`
  there.
- {{REPO}} on branch `swarm-review`: the integration tree. The coordinator reads it (`git log`,
  `git diff`, `git show`) to check fixes. Nobody edits files here. A dev whose change got APPROVED
  rebases its branch onto `swarm-review` once more, reruns the full tests if that moved its commits
  and waits for them to pass, then runs `git -C {{REPO}} merge --ff-only swarm-devN`; if that fails
  because another dev got there first, it rebases and retries. A REVERT is done the same way: revert
  commits on the dev branch, then fast-forward. Nobody switches branches, pushes, or resets this tree.

## Live status (everyone)
Report what you are doing with
  $SW/ev <YOUR_NAME> status <FINDING_ID or -> "<short message>"
at the start, about every few minutes of work, and when you finish. Names: R1..R{{N_REV}}, coordinator,
dev1..dev{{N_DEV}}. Keep messages under 100 characters, in plain words, for example "reading the policy
section". Devs also record their review-loop step whenever it changes:
  $SW/ev devN phase <FINDING_ID> <step>
with step one of implement, test, review-1, fix-1, review-2, fix-2, review-3, fix-3, review-4, fix-4,
approved, merge, blocked ("review-k": round k is with the dev's commit-reviewer; "fix-k": the dev
addresses round k's findings; "merge": rebase and fast-forward of swarm-review).

## Findings
A reviewer writes each finding to $SW/queue/<id>.json (id = its name and a running number, R3-2)
with the keys id, reviewer, title, tree (the worktree the reviewer read), severity, file, line, description, failure_scenario,
suggested_fix, and announces it with `$SW/ev R3 reported R3-2 "<title>"`. The coordinator's verdict
goes to $SW/val/<id>.txt, its check of the fix to $SW/val/<id>.check.txt.

## Severity
Use the scale in $SW/severity.md (Critical/High/Medium/Low/Info). Cosmetic issues (wording,
formatting, nits) are not findings: don't report them, and reject them.

## Accepted tradeoffs (report only with a genuinely new angle or a concrete exploit)

<!-- The dispatcher lists the documented owner decisions here, so that reviewers do not report
them again. Leave the heading with "none recorded" if there are none. -->

## Resource limits
- Reviewers and the coordinator run no test suites. Read and grep only.
- Every test run (devs and their commit-reviewers alike) goes through
  `$SW/tlock --as <your name> <test command>` (a dev's commit-reviewer uses `devN-reviewer`): it
  waits for a free test slot (there are {{SLOTS}} at the start; the number is in slots/count and may
  change), runs the command under a memory cap, and records the holder and the command in
  slots/1.json, slots/2.json and so on. One command per run; a run may wait for another
  agent's. Never start a test run any other way.
- A commit-reviewer reruns the test suite itself, but repeats a slow extra check (for example a
  mutation run) only for a check changed in the range; the dev's report lists the others.
- Never run commands that change the machine or anything outside the worktrees: no provisioning,
  deployment, deletion or hardware steps, whatever the repository's guide says about them.
