# seb-ai-skills

Skills and agents for Claude Code. A skill is a set of instructions, in a
`SKILL.md` file, that Claude loads into the current session when a task
calls for it; its work happens in that session, where you can watch it. An
agent is a separate Claude with its own instructions and tools, which the
session starts for one delimited task and which reports back; it does not
see the session's conversation, which is the point when the task is an
independent check such as a review.

This repository is a Claude Code
[plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces)
named `seb-ai-skills`. A marketplace lists plugins, which are what you
install. Each plugin here contains exactly one skill or one agent with the
same name, so you install only what you want.

| Skill | What it does |
| --- | --- |
| `write-clear-explainers` | Writes and reviews technical explainers and mixed explanation/procedure docs for readers who are technical but new to the system. |
| `test-gnosis-vpn` | Installs, funds and tests the latest Gnosis VPN client on a remote Linux server over SSH, or on a throwaway DigitalOcean VM that it destroys afterwards, after sending the funds back. It measures download speed and ping per second, or checks every exit. It runs commands as root on that server, changes its routes and firewall rules, and starts a watchdog on it: if the heartbeat you send over SSH stops for 6 minutes, the watchdog tears the VPN down, and after 12 minutes it reboots the server once. |
| `review-loop` | Gets everything not pushed yet reviewed by the `commit-reviewer` agent: fixes the findings it returns, resubmits to the same reviewer so that it remembers earlier rounds, and pushes only on `APPROVED`, for at most four rounds. |

| Agent | What it does |
| --- | --- |
| `commit-reviewer` | Reviews the commits not pushed yet, or a range you name, in the current repository and answers `APPROVED` or `NOT APPROVED` with findings. It reads the repository's guide for AI assistants (`AGENTS.md` or `CLAUDE.md`), judges the diff by that guide's priorities and invariants, runs the tests the guide names, and checks that the docs match the change. It only reads; it never edits, commits, or pushes. Because it starts without the author's reasoning, its verdict is not anchored on it. |

## Install

In a terminal, add the marketplace once, then install each plugin you want
as `<plugin>@seb-ai-skills`:

```sh
claude plugin marketplace add SCBuergel/seb-ai-skills
claude plugin install write-clear-explainers@seb-ai-skills
claude plugin install test-gnosis-vpn@seb-ai-skills
claude plugin install review-loop@seb-ai-skills
claude plugin install commit-reviewer@seb-ai-skills
```

Inside a running Claude Code session, use the same commands with `/plugin`
in place of `claude plugin`, for example
`/plugin install write-clear-explainers@seb-ai-skills`, then run
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
/review-loop:review-loop
```

## Get your commits reviewed

The `review-loop` skill and the `commit-reviewer` agent work together.
Install both. In the session where you develop, ask for a review: "get this
reviewed", or `/review-loop:review-loop`. That session then:

1. commits your work and determines the range to review: every commit not
   on the upstream branch yet;
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

## Update or remove a plugin

To get the latest version, refresh the marketplace listing, then update the
plugin. Start a new session or run `/reload-plugins` afterwards.

```sh
claude plugin marketplace update seb-ai-skills
claude plugin update write-clear-explainers@seb-ai-skills
```

To remove a skill or agent, uninstall its plugin. Remove the marketplace
only if you no longer want anything from it.

```sh
claude plugin uninstall write-clear-explainers@seb-ai-skills
claude plugin marketplace remove seb-ai-skills
```

## Maintain this repository

### How the repository is laid out

```text
.claude-plugin/marketplace.json    the marketplace: one plugin entry per skill or agent
skills/<skill-name>/SKILL.md       a skill, plus any files it uses
agents/<agent-name>/agents/<agent-name>.md
                                   an agent: its instructions under a front matter
evals/<skill-name>/<case>/         optional test cases for a skill
evals/run.sh                       runs a skill's test cases
```

Skills live under `skills/`, agents under `agents/`, one folder per plugin,
and the plugin is named after its folder. The two kinds differ one level
down. A skill is a folder with a `SKILL.md`, and Claude Code accepts a
pointer to that folder. An agent is a single `.md` file, and Claude Code
finds it only by scanning a folder named `agents/` inside the plugin; it
ignores a list of agent files in the manifest. So the agent's own folder
holds an `agents/` subfolder with the file, which gives the doubled name
`agents/<agent-name>/agents/<agent-name>.md`.

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

### Change a skill or an agent

1. Edit the files under `skills/<skill-name>/` or
   `agents/<agent-name>/agents/`.
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
claude plugin install <plugin-name>@seb-ai-skills
claude plugin details <plugin-name>@seb-ai-skills
unset CLAUDE_CONFIG_DIR
```

`claude plugin details` should show the version from `marketplace.json`
and, under `Component inventory`, only that plugin's component: `Skills (1)`
or `Agents (1)` followed by its name, and `(0)` for the other kind. If the
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
