# Seb's AI tools

Seb's AI tools are skills, agents and a hook for Claude Code. A skill is a set of
instructions, in a `SKILL.md` file, that Claude loads into the current
session when a task calls for it; its work happens in that session, where
you can watch it. An agent is a separate Claude with its own instructions
and tools, which the session starts for one delimited task and which
reports back; it does not see the session's conversation, which is the
point when the task is an independent check such as a review. A hook is
a check that Claude Code itself runs before a tool call, whatever the
session's instructions say, so it holds where a sentence in a skill may be
missed.

This repository is a Claude Code
[plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces)
named `sebs-ai-tools`. A marketplace lists plugins, which are what you
install. Each plugin here contains exactly one skill, one agent or one hook
with the same name, so you install only what you want.

| Skill | What it does |
| --- | --- |
| `write-clear-explainers` | Writes and reviews technical explainers and mixed explanation/procedure docs for readers who are technical but new to the system. |
| `test-gnosis-vpn` | Installs, funds and tests the latest Gnosis VPN client on a remote Linux server over SSH, or on throwaway DigitalOcean VMs that it destroys afterwards, after sending the funds back. It measures with [`gnosis_vpn-bench`](https://github.com/SCBuergel/gnosis_vpn-bench): speed and ping on one exit, a concurrent ladder, or a check of every exit. It runs commands as root on that server, changes its routes and firewall rules, and starts a watchdog on it: if the heartbeat you send over SSH stops for 6 minutes, the watchdog tears the VPN down, and after 12 minutes it reboots the server once. |
| `report-gnosis-vpn-results` | Reports, summarizes and compares Gnosis VPN test results from `gnosis_vpn-bench` runs in a standard way: it builds the report with the bench's renderer, puts versions, network, exit, VMs and date at the top of every summary, never reports a failure as zero speed or a short check as a throughput result, and pushes only allowlisted result files to a private results repository as soon as a run has produced them, before its VMs are torn down, without asking. |
| `review-loop` | Gets everything not pushed yet reviewed by the `commit-reviewer` agent: fixes the findings it returns, resubmits to the same reviewer so that it remembers earlier rounds, and pushes only on `APPROVED`, for at most four rounds. |
| `review-swarm` | Makes the session the supervisor of a swarm that coordinates through a shared work board instead of messages: reviewer agents audit one area of the repository each, short-lived validators judge and rate their findings, and dev agents fix them in parallel in their own git worktrees, each fix approved by the dev's own `commit-reviewer` and merged by a merge queue that tests the exact rebased commit. Claims are leases, so a stalled agent's work goes to another. Ships the briefs for every role, the board, a test-slot lock, and a terminal and a web dashboard. Needs `review-loop` and `commit-reviewer`. |
| `reflect` | Runs a retrospective over agent sessions and proposes changes to the setup that ran them: the environment, skills and workflows, never the work itself. A stdlib Python script scores friction in a session together with all its subagents, plus optional orchestration logs, and points to the exact transcript lines; the skill reads only those moments and lists fixes most severe first, each traced to a logged line. It only proposes. Based on the [`reflect`](https://github.com/Teebor-Choka/skills) skill by Teebor-Choka (MIT, licence kept verbatim in `skills/reflect/LICENSE.upstream`), extended for long multi-agent runs. |

| Agent | What it does |
| --- | --- |
| `commit-reviewer` | Reviews the commits not pushed yet, or a range you name, in the current repository and answers `APPROVED` or `NOT APPROVED` with findings. It reads the repository's guide for AI assistants (`AGENTS.md` or `CLAUDE.md`), judges the diff by that guide's priorities and invariants, runs the tests the guide names, and checks that the docs match the change. It only reads; it never edits, commits, or pushes. Because it starts without the author's reasoning, its verdict is not anchored on it. |

| Hook | What it does |
| --- | --- |
| `safe-bash` | Refuses Bash commands that find or kill processes by their full command line: `pkill -f` and `pgrep -f` (or `--full`), also as an `if` or loop condition, inside `sh -c` scripts and `eval`, and after common wrappers such as `sudo`, `timeout` or `watch` (the list is not complete). A command line like that inside a heredoc is refused too, even one written to a file, since a heredoc may be a script. The shell running such a command has the pattern in its own command line, so `pkill -f` kills that shell and the rest of the command's output is lost. The refusal tells the agent to kill by PID instead. It applies to every session once installed, including subagents and swarm agents. |

## Install

In a terminal, add the marketplace once, then install each plugin you want
as `<plugin>@sebs-ai-tools`:

```sh
claude plugin marketplace add SCBuergel/sebs-ai-tools
claude plugin install write-clear-explainers@sebs-ai-tools
claude plugin install test-gnosis-vpn@sebs-ai-tools
claude plugin install report-gnosis-vpn-results@sebs-ai-tools
claude plugin install review-loop@sebs-ai-tools
claude plugin install commit-reviewer@sebs-ai-tools
claude plugin install safe-bash@sebs-ai-tools
claude plugin install review-swarm@sebs-ai-tools
claude plugin install reflect@sebs-ai-tools
```

Inside a running Claude Code session, use the same commands with `/plugin`
in place of `claude plugin`, for example
`/plugin install write-clear-explainers@sebs-ai-tools`, then run
`/reload-plugins` to load what you installed. A session started after the
install loads it automatically.

## Use a skill

Claude uses a skill on its own when your request matches the skill's
description, for example "make this README easier to follow". To call a
skill directly, type `/<plugin>:<skill>`. Because each plugin and its skill
share a name, the name appears twice:

```text
/write-clear-explainers:write-clear-explainers
/test-gnosis-vpn:test-gnosis-vpn
/report-gnosis-vpn-results:report-gnosis-vpn-results
/review-loop:review-loop
/review-swarm:review-swarm
/reflect:reflect
```

## Get your commits reviewed

The `review-loop` skill and the `commit-reviewer` agent work together.
Install both. In the session where you develop, ask for a review: "get this
reviewed", or `/review-loop:review-loop`. That session then:

1. commits your work, with each check chained to its commit so a failing
   check stops it, and determines the range to review: every commit not on
   the upstream branch yet, named by its commit hashes;
2. starts `commit-reviewer` as a subagent with that range and at most a
   one-line pointer to what the change is about;
3. fixes the findings in the verdict, commits, and resubmits to the same
   reviewer, which remembers its earlier findings;
4. pushes when the verdict is `APPROVED`, or after four rounds without one
   stops and shows you the open findings.

You see each step in your session. The reviewer's own transcript is in the
subagent panel below the prompt and under `/tasks`.

To get a verdict without the loop, ask for the agent by name: "review with
commit-reviewer", or with a range, "review main..HEAD with commit-reviewer".
Give it at most a one-line pointer: a summary of what you believe the
change does would anchor it. Claude starts the agent only when you ask for
a review, never on its own.

The agent can also run as the main thread of its own session, for example
when your developing session runs without permission prompts and you want
the reviewer to prompt. Agents are named `<plugin>:<agent>`, like skills:

```sh
claude --agent commit-reviewer:commit-reviewer
```

Tell it to review, with or without a range; it prints the verdict there.

## Run a review swarm

The `review-swarm` skill scales a review across many agents. Install it
together with `review-loop` and `commit-reviewer`, which its devs use. In a
session at the repository, on a clean checkout of the branch to review, ask
for it: "run a review swarm for the security of this repository", or
`/review-swarm:review-swarm`. That session becomes the supervisor. It:

1. sizes the swarm from the machine's CPUs and memory (the limit is how many
   test runs go at once), splits the task into areas, and runs `swarm-init`,
   which creates a swarm folder with the tools, the protocol and a rules file,
   branch `swarm-review`, a read-only worktree of the starting point, a
   worktree for the merge queue, and one worktree per dev;
2. starts the web dashboard and posts its link, starts the merge queue, then
   the reviewers (read-only, one area each), the validators and the devs;
3. supervises without relaying anything. Every finding is a card on a shared
   work board, and each agent claims the next card for its role: validators
   judge and rate reported findings against the code and the rules file;
   devs fix valid ones in their worktrees and run `review-loop` on each fix;
   the merge queue rebases each approved fix onto `swarm-review`, tests that
   exact commit and fast-forwards; and the reviewer who reported the finding
   confirms the merged fix, or sends it back. A claim whose agent shows no
   sign of life for 15 minutes goes to another agent. The supervisor records
   your decisions in the rules file, starts the next review pass of an area
   when the board asks for it (a fresh reviewer with no memory of earlier
   passes, until 2 passes in a row find nothing at Medium or above, at most
   6 passes per area; ask for a single pass for a quicker check), and
   replaces stopped agents;
4. when every area is finished, has the whole range reviewed once more by one
   `commit-reviewer`, merges `swarm-review` into the source branch, pushes if
   your rules allow, and cleans the worktrees up.

You watch it live. In a terminal, `python3 $SW/dash.py` shows the swarm's
stage with the supervisor's latest notes, the agents with each area's open and
agreed findings, the findings pipeline (reported, validated, dev, merge and
verify, agreed), each dev's review-loop step, machine memory and load with who
holds each test slot, and the event feed; `python3 $SW/dashweb.py` serves the
same as a web page on http://127.0.0.1:8765/ with expandable findings and an
activity timeline. `$SW/board show` prints the board. `$SW` is the swarm
folder the session names. You can also talk to any agent directly, for
example to ask the optional `dash` agent for a change to the dashboards.

The swarm is file-based: the agents keep working on the board if the
supervisor session ends, and a new session can take over from the folder, as
the skill describes.

## Update or remove a plugin

To get the latest version, refresh the marketplace listing, then update the
plugin. Start a new session or run `/reload-plugins` afterwards.

```sh
claude plugin marketplace update sebs-ai-tools
claude plugin update write-clear-explainers@sebs-ai-tools
```

To remove a skill or agent, uninstall its plugin. Remove the marketplace
only if you no longer want anything from it.

```sh
claude plugin uninstall write-clear-explainers@sebs-ai-tools
claude plugin marketplace remove sebs-ai-tools
```

## Maintain this repository

### How the repository is laid out

```text
.claude-plugin/marketplace.json    the marketplace: one plugin entry per skill, agent or hook
skills/<skill-name>/SKILL.md       a skill, plus any files it uses
                                   (review-swarm: roles/ briefs, tools/ scripts, tests/, rules.md)
                                   (reflect: scan-sessions.py, tests/, LICENSE.upstream)
agents/<agent-name>/agents/<agent-name>.md
                                   an agent: its instructions under a front matter
hooks/<hook-name>/hooks/hooks.json a hook: when it runs, plus its script and tests/
evals/<skill-name>/<case>/         optional test cases for a skill
evals/run.sh                       runs a skill's test cases
```

Skills live under `skills/`, agents under `agents/`, hooks under `hooks/`,
one folder per plugin,
and the plugin is named after its folder. The two kinds differ one level
down. A skill is a folder with a `SKILL.md`, and Claude Code accepts a
pointer to that folder. An agent is a single `.md` file, and Claude Code
finds it only by scanning a folder named `agents/` inside the plugin; it
ignores a list of agent files in the manifest. So the agent's own folder
holds an `agents/` subfolder with the file, which gives the doubled name
`agents/<agent-name>/agents/<agent-name>.md`. A hook works the same way:
Claude Code reads `hooks/hooks.json` inside the plugin, which gives
`hooks/<hook-name>/hooks/hooks.json`; its command finds its script through
`${CLAUDE_PLUGIN_ROOT}`.

There is no `plugin.json` per plugin. Instead, each entry in
`marketplace.json` defines its plugin itself (`"strict": false`) and uses
the plugin's folder as `source`. No plugin uses the repository root as its
source: a plugin sourced there would load every skill under `skills/` and
every agent under `agents/`. Installing any plugin downloads the whole
repository, but Claude Code loads only that plugin's skill or agent; the
test in [Validate and test before pushing](#validate-and-test-before-pushing)
shows what a plugin loads.

Installed copies update only when the plugin's `version` in
`marketplace.json` changes. Pushing new text without a version bump reaches
nobody who already has the plugin.

### Add a skill

1. Create `skills/<skill-name>/SKILL.md`. The `name` in its front matter must
   match the folder name.
2. Add an entry to the `plugins` array in `.claude-plugin/marketplace.json`:

   ```json
   {
     "name": "<skill-name>",
     "source": "./skills/<skill-name>",
     "strict": false,
     "skills": ["./"],
     "version": "0.1.0",
     "description": "<one line on what it does>"
   }
   ```

3. Run the checks in [Validate and test before pushing](#validate-and-test-before-pushing),
   then commit and push.

### Add an agent

1. Create `agents/<agent-name>/agents/<agent-name>.md`: a front matter
   with `name` (matching the file name), `description` (when to use it),
   and `tools` (the tools it may use; a reviewer gets read-only ones),
   followed by its instructions.
2. Add an entry to the `plugins` array in `.claude-plugin/marketplace.json`
   as for a skill, with `"source": "./agents/<agent-name>"` and no
   `skills` line.
3. Run the checks in [Validate and test before pushing](#validate-and-test-before-pushing),
   then commit and push.

### Add a hook

1. Create `hooks/<hook-name>/hooks/hooks.json` with the event, the matcher
   and a command that runs a script next to it through
   `${CLAUDE_PLUGIN_ROOT}`, and a test under `hooks/<hook-name>/tests/`.
2. Add an entry to the `plugins` array in `.claude-plugin/marketplace.json`
   as for an agent, with `"source": "./hooks/<hook-name>"`.
3. Run its tests, for example `python3 -m pytest hooks/<hook-name>/tests`,
   and the checks in [Validate and test before pushing](#validate-and-test-before-pushing)
   (`claude plugin validate` has no check for a hook folder; the test
   install shows whether the hook is found), then commit and push.

### Change a skill, an agent or a hook

1. Edit the files under `skills/<skill-name>/`,
   `agents/<agent-name>/agents/` or `hooks/<hook-name>/`.
2. Bump that plugin's `version` in `.claude-plugin/marketplace.json`.
3. Run the checks in [Validate and test before pushing](#validate-and-test-before-pushing),
   then commit and push.

### Validate and test before pushing

In a terminal at the repository root, check the manifest, the skills, and
the agents:

```sh
claude plugin validate --strict .
claude plugin validate --strict skills
claude plugin validate --strict agents
```

Each command should end with `Validation passed`. If one reports errors,
fix them and run all three again.

Then install the plugin from your working copy, which includes uncommitted
changes. The local marketplace has the same name as the GitHub one, so run
the test with a temporary Claude Code config directory. That leaves your own
installed plugins untouched:

```sh
export CLAUDE_CONFIG_DIR=$(mktemp -d)
claude plugin marketplace add ./
claude plugin install <plugin-name>@sebs-ai-tools
claude plugin details <plugin-name>@sebs-ai-tools
unset CLAUDE_CONFIG_DIR
```

`claude plugin details` should show the version from `marketplace.json`
and, under `Component inventory`, only that plugin's component: `Skills (1)`
or `Agents (1)` followed by its name, or `Hooks (1)` followed by its event,
and `(0)` for the other kinds. If the
component is missing, check that the entry's `source` points at the right
folder and that the `name` in the front matter matches the folder or file
name. If another component is listed as well, the entry's `source` is too
wide; see the layout rules above.

### Run a skill's test cases

Some skills have test cases under `evals/<skill-name>/`. Each case is a
`prompt.md` plus `graders/*.md` files that describe what a good answer must
do. `claude plugin eval` runs each prompt with and without the skill and
has a model grade the answers. The runner needs a `plugin.json`, which this
repository does not have, so `evals/run.sh` builds a temporary plugin from
your working copy and runs the cases there.

In a terminal at the repository root:

```sh
evals/run.sh write-clear-explainers
```

Extra options go to `claude plugin eval`, for example `--case 'narrow*'` to
run one case or `--runs 1` for a cheaper check. Each run uses your Claude
account; a full run of both `write-clear-explainers` cases costs about 3 US
dollars. The script grades with Sonnet, because the default Haiku grader
gave inconsistent verdicts on these cases. The summary table shows scores
with the skill, without it, and the difference.

## License

[MIT](LICENSE)
