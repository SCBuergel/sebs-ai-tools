---
name: reboot-resume
description: Persist the Claude Code sessions running on this Linux machine, interactive tabs and running background (--bg) sessions, and bring them back after the machine or VM restarts, each in its own terminal tab in the right folder with the same flags. Use when the user wants to persist or save sessions, survive a reboot or restart, restore or reopen sessions after a restart, see what would come back, keep a session out of the restore, or install or remove the automatic snapshot and restore.
---

# Reboot resume

`reboot-resume.py` (in this skill's folder; stdlib Python 3) records the sessions running on this machine and reopens them after a restart, in one `xfce4-terminal` window with one tab per session, titled with the session's name. Background sessions come back as normal tabs in the foreground. Once installed it runs by itself: a systemd user timer takes a snapshot every 3 minutes, and an XDG autostart entry restores at login. It never reboots anything.

Run `python3 <skill folder>/reboot-resume.py <command>` before it is installed. After `install`, the same commands are available from `~/.local/share/reboot-resume/reboot-resume.py`.

| Command | What it does |
| --- | --- |
| `snapshot` | Records the sessions now, under `~/.local/state/reboot-resume/snapshots/`. |
| `restore --dry-run` | Prints which sessions it would open or skip, and why, each tab's `cd … && claude --resume …` line, and the exact terminal command. Starts nothing. |
| `restore` | Opens the recorded sessions that are not running yet. |
| `restore --print` | Prints copy-paste `cd … && claude --resume …` lines instead of opening a terminal: the manual fallback. |
| `restore --only NAME…` | Restores only these sessions, by name or id prefix of 8 or more characters. |
| `restore --snapshot latest` | Uses the newest snapshot of this boot instead of the newest from an earlier boot (the default). |
| `exclude NAME…` / `include NAME…` | Keeps sessions out of every restore, or puts them back. |
| `install` / `uninstall [--purge]` | Adds or removes the timer and the autostart, for this user only. `--purge` also deletes the snapshots and the config. |
| `status` | Shows what is installed, the latest snapshot, and whether this boot was restored. |

## What it records

- **Only running sessions.** A session is recorded only if it runs when the snapshot is taken. A session you close drops out of the next snapshot.
- **Interactive sessions:** from `claude agents --json` and `~/.claude/sessions/` (either is enough). Their flags come from the process command line.
- **Background sessions:** a job in `~/.claude/jobs/*/state.json` counts only while its session runs: either a live session has its id, or a `claude` process names it with `--resume` or `--session-id`. Its flags come from the live session's own process when it has one, and otherwise from the job's `respawnFlags` (always for a job found only through such a process). A job's folder stays on disk after it finishes, or after its daemon has died, and such jobs are never recorded, however recent they are. The dry run lists them as "not running at snapshot time".
- **Folder:** `claude --resume` finds a transcript by the current folder. So the folder is the cwd recorded in the transcript whose encoding matches the transcript's project folder, which may be a git worktree the session moved into. If none exists, the session's own folder is used and the dry run notes it.
- **Flags and model:** a session without `--model` gets the model its transcript last used. All other options are kept as the session had them, minus the ones that pick or start a session (`--resume`, `--continue`, `--session-id`, `--bg`, `--worktree`, `--print` and similar). A prompt given on the command line is never kept, so a restore does not send it again.
- **Nothing secret:** a snapshot holds ids, names, folders, flags and models, never the environment or tokens.
- **User services:** running services from the user's own unit files are listed in the dry run with whether they are enabled. Restore never starts them.

## Safety rules

- **Idempotent.** A restore skips every session that is already running, and the login restore runs once per boot, so a second login does not reopen sessions you closed.
- **Correct after an unplanned restart.** The default restore uses the newest snapshot from an earlier boot. After boot, the timer writes no snapshot until the restore has run (or for 30 minutes), so a restart does not replace the record with an empty one. The newest snapshot of each of the last 5 boots is always kept.
- **Failures stay visible.** Each tab prints its command. If `claude` fails or the folder is gone, the tab says so and keeps a shell open. The other tabs open regardless.
- **Login wait.** At login the restore waits 15 seconds, then up to 3 minutes for `api.anthropic.com` to be reachable, then opens the tabs anyway.
- **Extras are opt-in.** Other commands worth restarting go in `~/.config/reboot-resume/config.json` as `"extras": [{"name": …, "cwd": …, "cmd": …}]`. They are only listed unless `"start_extras": true`. Extras are not checked for already running, and `--only` leaves them out.
- **Install touches only these files:**
  - `~/.local/share/reboot-resume/`
  - `~/.config/systemd/user/reboot-resume-snapshot.{service,timer}`
  - `~/.config/autostart/reboot-resume.desktop`
  - `~/.config/reboot-resume/`

  It never edits Claude Code settings or system files.

## Steps

1. When the user asks what would come back, run `snapshot`, then `restore --snapshot latest --dry-run`. Show them the list, and ask whether any session should be excluded.
2. When the user asks to set it up, run `install`, then `status`. Tell them it takes effect from the next restart.
3. When the user asks to test it without restarting:
   1. Run a dry run as in step 1.
   2. Have the user start a throwaway session and send it one message, for example `mkdir -p ~/rr-test && cd ~/rr-test && claude -n rr-test`.
   3. Run `snapshot` while that session runs, since only running interactive sessions are recorded. Then have the user exit it.
   4. Run `restore --snapshot latest --only rr-test`. One new window should open, with one tab that continues that conversation.
4. After a restart, if the tabs did not appear, check `status` and `~/.local/state/reboot-resume/restore.log`, then use `restore` or `restore --print`.

Linux only, and the window needs `xfce4-terminal`. Without it, a restore fails with a message, and `restore --print` gives the commands.
