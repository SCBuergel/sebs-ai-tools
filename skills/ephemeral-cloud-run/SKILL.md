---
name: ephemeral-cloud-run
description: Rent a cloud machine (GPU or CPU, on DigitalOcean, RunPod, Vast.ai or a similar provider) for one job, run the job, copy its results back and delete the machine, without leaving anything billing. Use when a job needs hardware this machine lacks (a GPU render, training, a large build or benchmark), when the user asks to rent, spin up or use a cloud GPU or VM for a task, or when writing a script that does so.
---

# Ephemeral cloud run

A rented machine bills until it is deleted. On most providers that
includes while it is stopped. So every run follows one rule: **the machine
is deleted on every exit path, and an independent deadline deletes it if
the run itself cannot.** The job itself (what runs on the machine) is the
user's. This skill covers renting, watching, copying back and deleting.

`scripts/deadline` (in this skill's folder; stdlib Python 3) is the
independent deadline: `deadline start NAME SECONDS -- <delete command>`
arms a systemd user timer (or, without systemd, a detached process). It
runs the delete command even if the agent, its session or the run script
dies; `deadline cancel NAME` disarms it, and `deadline list` shows what is
armed. It lives on this machine, so it does not survive a restart of this
machine. Without lingering (`loginctl enable-linger`, which `start` warns
about) it also stops when the user's last login session ends. Keep this
machine up and logged in during a run. After a restart, run `deadline list`
first: a deadline shown as "never fired" means its machine may still be
billing, so delete it at once. `references/providers.md` has the provider quirks seen so far.

## 1. Agree the job's limits with the user

Before renting anything, write down with the user:

- **Hardware allowlist:** the GPU models (or CPU, RAM and disk sizes) the
  job may run on, best first. Never rent outside it.
- **Price cap:** the most per hour, including storage. Refuse offers above
  it, and recheck the real price after creation, since catalogue prices and
  billed prices can differ.
- **Time cap:** the longest the machine may live. The deadline uses it.
- **Budget:** price cap × time cap, said out loud. Renting costs money, so
  the user approves it: start only with their go-ahead for this run.
- **Results:** which files come back, and where they go.

Keep the provider's API token in a mode-600 file. The `secret-store` skill
does this without it passing through the chat. Read the token inside the
command that uses it, never on a command line.

## 2. Probe the account before renting

Test each permission with a harmless call, and stop and tell the user when
one fails:

- **List or catalogue:** can the token read offers, sizes, regions and
  prices? (A list call.)
- **Delete:** can it delete? Where the provider lists a token's scopes or
  permissions, read them. Otherwise delete something that does not exist,
  such as a made-up id: "forbidden" proves the token cannot delete, but
  "not found" proves it only on APIs that check permission before looking
  the id up, so then also delete the harmless object from the create probe
  below. A token that can create but not delete must never rent.
- **Create:** can it create? Use the cheapest harmless object the provider
  has, for example an SSH key, which you then delete again (that delete is
  also the real delete probe). Some providers
  also need one on the account before an image boots with SSH.

Then pick the offer: allowlisted hardware only, under the price cap, from
a reliable or verified host where the provider rates hosts. Availability
changes by the minute, so reread the catalogue between attempts instead of
retrying one offer.

## 3. The run script

Write one script per run, kept in the session's scratch folder, with:

- **Teardown on every exit path:** a `trap … EXIT` that deletes the
  machine, plus `INT`, `TERM` and `HUP` traps that exit through it. Record
  the machine's id in a file the moment it is created, so teardown and the
  deadline know what to delete.
- **The deadline, armed right after creation:** `deadline start <run> <time
  cap in seconds> -- <delete command>`. The delete command reads the token
  from its file, and a machine that is already gone counts as success.
  Cancel the deadline only after the delete is verified.
- **Delete, never stop.** Verify that it is gone: the provider's API
  answers "not found" for the id. A delete call that returned success is
  not enough. If it is not verified gone, print a line that cannot be
  missed, with the id and the provider's console address, and tell the
  user at once.
- **Per-step timing:** print `STEP <name> <seconds>` after each step
  (create, boot, SSH ready, upload, setup, job, download, delete), and the
  machine's total lifetime. Those lines are what the run cost, and they
  show where the next run can save time.
- **Copy results back as they appear,** not only at the end. Copy each file
  to `<name>.part`, then rename it, so a half-copied file is never taken for
  a result. A run that dies late then still leaves what it produced.
- **Watch through a log:** run the job on the machine detached (`nohup` or
  `tmux`), write its output to a log there, and poll that log over SSH.
  Then a dropped SSH connection does not kill the job.
- **A job exit marker:** the job's last line, for example `JOB-EXIT <code>`,
  so the script knows the job ended and how.

Use a fresh SSH key per run, and remove any key the run added to the
account during teardown.

## 4. During and after

- Watch the run with one background watcher on the run log, and post each
  `STEP` line to the user as it arrives.
- After the run, confirm these and say so:
  - the machine is verified gone;
  - `deadline list` shows nothing left for the run;
  - the provider's instance list for the account is empty, or holds only
    machines that were there before the run.
- Report the steps' timings, the machine's lifetime and the cost (price ×
  lifetime, and the provider's billed amount if its API gives one).
- When a provider or image needed a workaround, add it to
  `references/providers.md`, dated, so the next run starts from it.

## Never

- Rent before the user approved this run's budget.
- Rent outside the allowlist or above the price cap.
- Leave a machine stopped instead of deleted.
- Put a token into a command line, a file in the repository, a commit, a
  memory or the chat.
