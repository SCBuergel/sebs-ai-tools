---
name: report-gnosis-vpn-results
description: Report, summarize or compare Gnosis VPN test results in the standard way, from the result files of a gnosis_vpn-bench run (concurrent ladder, per-client bandwidth, exit sweep, availability checks), and push them to the private results repository after the user confirms. Use when asked to report, summarize, write up or compare Gnosis VPN results, benchmarks, ladders, sweeps or exit checks, or to publish them.
---

# Reporting Gnosis VPN test results

The standard lives in gnosis_vpn-bench, not here: the schema of `result.json`
(version 1.0, in the top-level `schema_version`; read `docs/result-schema.md`)
and its one report renderer. This skill says which commands to run and how to word what you write around them. Check
`./gnosis_vpn-bench --help` first; if a command or field named here is
missing or differs, follow gnosis_vpn-bench and tell the user.

A run is a directory with `run.toml`, by default
`~/.local/state/gvpn-bench/runs/<id>/`, and one result set per target in
`results/<target>/`. `RUN` below is its id or path. Runs stay outside the repository because
gnosis_vpn-bench is public.

## 1. Recordings and reports

A **recording** is one run against one exit: in the private results
repository (`[results] repo` in `~/.config/gvpn-bench/config.toml`) it is
`runs/<date>-<HHMMSS>Z-<target>-<profile>[-historical]/` with `result.json`,
`provenance.json`, `report.html`, `README.md` and `charts/`. A **report**
covers one or more recordings across exits, runs and versions:
`reports/<name>/`, with `report.json` (`title`, `recordings`, `links`), a
`README.md` with SVG charts that GitHub renders, and the same content as
`report.html`. The repository's `README.md` indexes them all.

Recordings with the same exit address, network, profile and package version
form a **series**. A report shows repeated recordings of a series as their
mean with min-max bars, and a rung that failed in some of them as "k of n
not measured: <status>". Its sections are fixed: concurrent bandwidth (total
Mbit/s against concurrent clients, one line per series), per client,
availability and failures, provenance.

Do not hand-write a report or edit a rendered one. If a section is wrong,
fix the data or the renderer in gnosis_vpn-bench, then `results rebuild`.
To render a run locally before pushing: `./gnosis_vpn-bench report RUN`.

## 2. Provenance in every summary

Every summary you write, including a chat reply, starts with these, read from
`result.json`:

- client version and package version, which differ; give both
  (`provenance.client.ctl_version`, `.package_version`)
- network: `jura-prod`, `piz-palu-prod` or `piz-palu-dev`, and profile
  (`provenance.profile.network`, `.name`)
- exit address and label (`provenance.target.exit_address`, `.label`,
  `.location`)
- provider, region and the size of each VM by role
  (`provenance.infrastructure`)
- start and end of the run in UTC (`run.started_at`, `run.finished_at`)
- for a run with `run.reproducible: false`, that it is imported historical
  data (`run.historical_source`) whose raw inputs are incomplete

If one is missing, write "not recorded"; never fill it in from memory or from
another run.

## 3. Wording

- A failure is never a speed of zero. A rate that was not measured is `null`
  with a status: write "not measured" and the status. A rung whose `status`
  is `not_run` or `failed` has no throughput; its `detail` says why.
- A short availability check (a few MiB and some pings) is never a ladder or
  throughput result. Call it a check and give its size.
- Say what was NOT measured: rungs that did not run, exits not reached,
  missing ping samples.
- One trial is one sample. It cannot separate an exit's own limit from its
  relay paths, TCP startup or network changes during the run. Say so whenever
  a conclusion rests on one trial.
- Name statuses exactly as recorded, from the fixed list in
  `docs/result-schema.md`, such as `UnacceptablePixParams`,
  `DestinationNotFound`, `timeout` or `control_timeout`. `connected` is the
  only success. Do not paraphrase them into "failed to connect".
- `concurrent.baseline[]` holds direct downloads without the VPN. It shows the
  origin's capacity, not a VPN result.
- Keep host problems apart from exit failures. For example, a local netlink
  `ENOBUFS` during killswitch setup makes a check inconclusive, not failed.

## 4. Compare runs

Compare only recordings of the same exit address, network and profile
(`provenance.target.exit_address`, `provenance.profile.network`,
`provenance.profile.name`), ordered by `run.started_at`; within ladders,
compare `concurrent.rungs[].total_mbps` rung by rung. Different
`provenance.client.package_version` values are different series: that is how
to compare client versions. Find recordings through `reports/*/report.json`
and `runs/*/result.json`, selecting by these keys, not by folder name.

To show the comparison, build a report over the recordings rather than a
table of your own (section 5, `results combine`). Give each series' versions
next to its numbers, and say how many recordings a mean rests on. If
anything else differs, such as region, VM size, test specification or host
adjustments, name the difference and do not attribute the change to the
client version.

## 5. Push and combine

Run these in the gnosis_vpn-bench checkout. Each one scans, commits and asks
before pushing:

```bash
./gnosis_vpn-bench results push RUN [RUN ...] --report-name N --title T --dry-run
./gnosis_vpn-bench results push RUN [RUN ...] --report-name N --title T
./gnosis_vpn-bench results combine REC [REC ...] --name N --title T   # recordings already in the repo
./gnosis_vpn-bench results link REPORT LABEL URL                      # e.g. the Claude-app page
```

`push` adds each run's recordings and one report over all of them. It copies
only allowlisted files and refuses when its scan finds keys, tokens or IP
addresses, or when the repository is public. Never copy files into the
results repository by hand.

Show the user the dry run and push after they confirm. If the user has said
to push every run right away, push as soon as the results exist, without
waiting for teardown, and still never pass `--yes` unless they said so.

## 6. After a run

Give the user a table per exit: rung, clients, total Mbit/s, per-client
Mbit/s, mean ping and status, with the provenance from section 2 above it.
Then link the report on GitHub,
`https://github.com/<owner>/<results-repo>/tree/main/reports/<name>`, once
pushed. If the user wants a Claude Artifact of the report's `report.html`,
publish it and attach its link with `results link`.

## Privacy

Never put any of these into a report, a summary or the results repository:
logs, VM IP addresses, SSH or API keys, tokens, faucet codes, node identity
files, payment state or Safe recovery data. If a result file contains one,
stop and tell the user instead of pushing.
