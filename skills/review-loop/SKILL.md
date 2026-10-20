---
name: review-loop
description: Get everything not yet pushed reviewed by the commit-reviewer agent, fix its findings, resubmit to the same reviewer, and push only on APPROVED. Use when the user asks for a review loop, to "get this reviewed", or to push after review. Not for reviewing yourself.
---

# Review loop

This skill drives a review of the current branch's unpushed commits by the
`commit-reviewer` agent, started as a subagent of this session and resumed
for every round so that it remembers its earlier findings. Every step runs
in this session; the reviewer's own transcript is in the subagent panel
below the prompt and under `/tasks`.

## 1. Commit

Finish the change, run the repository's tests as its guide requires, and
commit locally. The review covers every commit that is not on the
upstream branch yet: `@{upstream}..HEAD`. If the branch has no upstream,
use the remote's default branch, for example `origin/main..HEAD`. If that
range is empty, tell the user there is nothing to review and stop. The user
may name another range instead; then use that.

## 2. Start the reviewer

Start one `commit-reviewer:commit-reviewer` subagent with a prompt whose
first line is `Review request: <range> in <repository path>`, followed by at
most one line saying what the change is about. Do not describe how it works
or why it is correct: that would anchor the reviewer on your view. Keep the
agent's id; every later round goes to this same agent.

## 3. Act on the verdict

- `APPROVED <range>`: push, then tell the user what was pushed and any
  `Optional:` items you left alone.
- `NOT APPROVED <range>`: fix each finding in the code, never by weakening
  a check or an invariant the repository's guide names. Run the tests, check
  with `git branch --show-current` that you are still on the branch you
  started on (a reviewer that switched the checkout would otherwise put the
  fix on a detached HEAD), and commit. Then resume the same reviewer with SendMessage: the range,
  recomputed as in step 1 so it includes the fix commits, plus one line per
  earlier finding saying fixed, or why you think it is wrong. Never start a
  second reviewer for the same batch; a fresh one would not know what it
  already found.

Repeat until the verdict is APPROVED, for at most four review rounds in
all. After the fourth NOT APPROVED, stop and show the user the open
findings; the decision is theirs.

Never push without an APPROVED for the exact range that is pushed.
