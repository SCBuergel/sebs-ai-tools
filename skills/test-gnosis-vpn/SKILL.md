---
name: test-gnosis-vpn
description: Install, fund and test the latest Gnosis VPN client on a remote Linux server over SSH without locking yourself out. Use when asked to deploy, upgrade, fund (faucet codes or a direct wxHOPR transfer), measure (download speed and ping, a concurrent ladder, or a quick check of every exit), or exercise Gnosis VPN on a server, including throwaway cloud VMs that are created for the test and destroyed afterwards with their funds returned, or when a tool that drives gnosis_vpn-ctl needs re-validating against a new client version.
---

# Testing Gnosis VPN on a remote server

The measuring is done by gnosis_vpn-bench (`~/gnosis_vpn-bench`, or clone
`SCBuergel/gnosis_vpn-bench`). This skill holds the safety rules around it
and the manual steps for a server the bench did not create. There are two
cases:

- **Throwaway VMs** (a new machine for the test): use a bench remote run. It
  creates the VMs, funds them, installs the guards, measures, returns the
  funds and deletes the VMs. `./gnosis_vpn-bench execute RUN` does all of it
  for every target of the run and tears down even after an error or Ctrl-C;
  `--keep` keeps the VMs until the deletion timer fires. Each step also
  runs on its own. Read its `docs/runbook.md` first; sections 0,
  4 and 6 below still apply.
- **A server the user names**: install, fund and guard it by hand (sections
  0-4), then measure with `./gnosis_vpn-bench --ssh root@HOST <mode>`.

The server address comes from the user each time. Never write it into files,
commits or memory. To report the results, use the `report-gnosis-vpn-results`
skill.

## 0. Golden rule: connecting the VPN kills your SSH session

When a tunnel comes up, the client:
- adds `0.0.0.0/1` + `128.0.0.0/1` via the TUN, so everything except HOPR peer IPs and RFC1918 goes into the tunnel, and
- installs a **killswitch**: nftables table `inet gnosis_vpn_ks` with default-drop `input`/`output`/`forward` chains. It allows only loopback, DHCP/NDP, the tunnel interface, HOPR peers and (unless `lan_lockdown`) private ranges.

So SSH from a public IP dies, and a route pin alone is NOT enough. The client re-applies the table atomically (add/del/add) whenever peers change, so a one-shot `nft insert` gets wiped.

**Before the first `connect`, two guards must run on the server.** A bench
remote run starts them in its `connect` step, together with a local
heartbeat. `--ssh` mode does not: on a server the user names, install them
yourself from the scripts next to this file.

1. `scripts/ssh-keeper.sh MYIP WAN_GW WAN_DEV`: every second, it pins `MYIP/32` via the WAN gateway and re-inserts `ip saddr/daddr MYIP accept` into the killswitch chains when missing.
2. `scripts/deadman.sh`: watches `/run/gvpn-deadman.alive`. If stale for more than 6 min, it disconnects, stops the worker and deletes the killswitch table. If stale for more than 12 min, it reboots once. `/run` is cleared on boot, so it can't loop.

```bash
MYIP=$(curl -s -4 https://ifconfig.me)                  # locally
ssh root@HOST 'ip route show default'                    # -> WAN_GW, WAN_DEV
scp scripts/*.sh root@HOST:/root/gvpn-guard/            # or: tar c | ssh tar x (rsync may be missing)
ssh root@HOST 'chmod +x /root/gvpn-guard/*.sh; touch /run/gvpn-deadman.alive;
  systemctl reset-failed gvpn-ssh-keeper gvpn-deadman 2>/dev/null;
  systemd-run --unit=gvpn-ssh-keeper /root/gvpn-guard/ssh-keeper.sh MYIP WAN_GW WAN_DEV;
  systemd-run --unit=gvpn-deadman   /root/gvpn-guard/deadman.sh'
```
Use transient `systemd-run` units, so a reboot leaves nothing behind. The `reset-failed` lets you start the guards again on a host where they ran before. Then keep a **local heartbeat** running in the background for the whole session:
```bash
while true; do timeout 20 ssh -o ConnectTimeout=10 root@HOST 'touch /run/gvpn-deadman.alive' && echo "$(date +%T) ok" || echo "$(date +%T) FAIL"; sleep 60; done
```
Test the guards with one manual connect, polling SSH every ~10 s. `journalctl -u gvpn-ssh-keeper` should show "inserted killswitch allow".

Run anything long under `tmux new -d -s NAME "cmd > log 2>&1"` on the server, then poll the log file.

**Teardown order matters:** first disconnect and stop the worker, then `systemctl stop gvpn-deadman gvpn-ssh-keeper`, and only then stop the local heartbeat. Otherwise the deadman reboots the box. When killing the heartbeat with `pkill -f`, use a pattern that does not match your own shell command, or you kill yourself (exit 144). Bench's `recover` keeps this order.

If you are locked out anyway, the user must reboot the machine or run `gnosis_vpn-ctl disconnect` from the provider console. After a reboot the worker comes up idle, so SSH works again.

## 1. Get a server

**Throwaway VMs:** `./gnosis_vpn-bench plan --profile PROFILE --target TARGET`,
then `provision RUN`. Provision schedules a deletion timer before the first
VM exists. It costs money and asks first; pass `--yes` only when the user
has decided in advance. Change VM sizes, region and client count in the
run's `run.toml`. Pick the region closest to the exit under test, and say so
if you deviate from what the user asked for. If a bench step fails, stop and
show the user its output and `status RUN`; do not create or delete VMs by
hand around it.

**A server the user names:** survey it first.
```bash
systemctl list-units --type=service --state=running; docker ps -a; ss -tlnp
dpkg -l gnosisvpn; gnosis_vpn-ctl --version; ls /etc/gnosisvpn; systemctl is-enabled gnosisvpn
```
HOPR relays usually run as `hoprd` docker containers. Stop only what the user said to stop. Note the existing config: `/etc/gnosisvpn/config.toml` and `gnosisvpn-dynamic.env`, where `GNOSISVPN_HOPR_BLOKLI_URL` shows the network, e.g. `blokli-jura.prod.hoprnet.link` means jura-prod.

## 2. Find and install the latest version

Bench runs install the package pinned in their profile. On a server the user names, the authoritative source is the APT index:
```bash
curl -fsSL https://download.vpn.gnosis.eth.limo/linux/apt/dists/stable/main/binary-amd64/Packages | grep -E '^(Version|Filename)'
```
Install or upgrade with the official script. It sets up the keyring and apt source and handles channel downgrades:
```bash
curl -fsSL https://download.vpn.gnosis.eth.limo/linux/install.sh | bash
# options: --channel=stable|snapshot|experimental  --network=<name>  --reset-identity
```
- The **package** version (e.g. 0.95.2) differs from the **client** version that `gnosis_vpn-ctl --version` and `info` report (e.g. 0.96.3). Report both.
- Config is kept (dpkg confold) and backed up as `config.toml.backup.<ts>`. Node identity is kept unless `--reset-identity`, so an old node may already be funded.
- The service is enabled and running after install, but the **worker is idle** ("Worker offline").

## 3. Drive the client (gnosis_vpn-ctl)

Always check `gnosis_vpn-ctl --help` first. The CLI changes between versions. As of 0.96:
```bash
gnosis_vpn-ctl start-client 30m      # required; keep-alive resets on each command (not info/ping/stop-client)
gnosis_vpn-ctl -o json status        # global -o plain|json|yaml  (old --json flag is gone)
gnosis_vpn-ctl -o json balance       # node/safe balances, info.node_address, funding_status
gnosis_vpn-ctl connect <DestinationId>   # e.g. Austria, returns before the tunnel is up
gnosis_vpn-ctl disconnect
gnosis_vpn-ctl info | nerd-stats | telemetry | check-update | stop-client
```
The v5 (experimental) client runs one worker per user: control it with `runuser -u <user> -- gnosis_vpn-ctl …`, or bench's `--ctl-user USER`; root cannot own it.

Use a short keep-alive such as `30m`, never days. If you lose access, the worker then stops by itself and takes the tunnel down. Only commands reset the keep-alive; tunnel traffic does not, so a transfer that outlasts the keep-alive is cut when the worker stops.

Detecting an idle worker: `-o json status` still exits **0** with `run_mode: "NotRunning"`. Inspect the JSON, not exit codes. After `start-client`, poll until `run_mode` is `Running`; a fresh identity goes through `PreparingSafe` (waiting for funds, section 4) and `DeployingSafe` first. Exit code `69` shows up for worker-only commands while offline, and `76` means disconnect while not connected (harmless).

A fresh node has no channels: its destinations show `NeedsPeering`, then `NeedsChannel`, until the first connect opens them on-chain, which took 30-75 s on 0.96.3. Allow at least 180 s before calling a first connect a timeout.

When behaviour is unclear, read the source instead of guessing (`git clone --depth 50 https://github.com/gnosis/gnosis_vpn-client`): `gnosis_vpn-lib/src/command/mod.rs` (status types), `gnosis_vpn-ctl/src/cli.rs` (CLI), `gnosis_vpn-lib/src/killswitch/` (firewall), `gnosis_vpn-root/src/routing/linux.rs` (routes).

## 4. Fund the node

Bench's `fund RUN` backs up each identity, shows the amounts and asks before
sending from the funding Safe configured for bench. For a server the user
names, fund by hand as below.

The address to fund is the **node address**, i.e. "Gnosis VPN Address" in the UI. It is not the safe address. While the worker is in `PreparingSafe`, read it from `status` → `PreparingSafe.node_address`, together with `balance_recommendation` (how much wxHOPR to send). Once the node is running, it is in `balance` → `info.node_address`.

Back up the identity (`/var/lib/gnosisvpn/.config/gnosisvpn-hopr.{id,pass,safe}`, and for v5 its payment state) locally into a mode-700 directory before funding. The Safe is controlled only by this key; lose the key and you lose the funds.

There are two ways to fund. Ask the user which one, unless they or your memory already name a source.

**Faucet code.** The faucet API is a single POST (re-grep `fetch(` in `https://faucet.vpn.gnosis.eth.limo/` `assets/index-*.js` if it has moved):
```bash
curl -sS -X POST https://cfp-funding-api-656686060169.europe-west1.run.app/api/cfp-funding-tool/airdrop \
  -H 'Content-Type: application/json' \
  -d '{"address":"<NODE_ADDRESS>","code":"<SECRET CODE>"}'
```
- Success looks like `{"success":true,"message":"Successfully sent 0.005 xDAI and 241.01 wxHOPR ..."}`. Errors come back in `.error`.
- Codes are single-use. Never write codes into files or memory. Mark a code as used in the user's ledger only when the faucet actually judged it (success, or "The code has no uses left"), never on `Rate limit exceeded`.
- Stockpiled codes go stale. If several in a row are spent, stop and ask the user for fresh codes or a direct transfer rather than burning through the file.

**Direct transfer.** Send at least `balance_recommendation.wxhopr` (241 wxHOPR on 0.96.3) and 0.005 xDai to the node address on Gnosis Chain. Simulate first and send only what the user agreed to. wxHOPR is `0xD4fdec44DB9D44B8f2b6d529620f9C0C7066A2c1`; the public RPC `https://rpc.gnosischain.com` works.

**Never retry an ambiguous transaction** (sent, but no receipt): look it up by hash and reconcile its receipt and nonce first, or you may pay twice. Bench journals every transaction and stops on one.

Afterwards, check `balance`: `funding_status.traffic/gas` should be `Good` and the deficits `null`.

## 5. Measure

Bench modes, run from the bench checkout. On a server the user names add
`--ssh root@HOST` (and `--ctl-user USER` for v5), with the guards and
heartbeat from section 0 running; on a bench run's VM add `--run RUN --on
clientN`. `--exit ADDRESS` or `--target NAME` limits a mode to one exit.

- Concurrent 1 → 2 → 3 client ladder: `ladder RUN`.
- Every exit once: `sweep RUN` on a bench run (`--exit ADDR` for some),
  or `locations --runs 1` over `--ssh`.
- Speed and ping over time on one exit: `curl [URL]`, `ping`, or `ping-load`.
- Download sizes and gaps: `ramp`, `repeated`, `gap`.

`./gnosis_vpn-bench <mode> --help` has the parameters. Bench tests the exit it
was given, by address; never substitute another exit or a direct download for
a failed tunnel.

For a tool that drives the client, run each of its commands once, briefly,
with shortened gaps instead of full multi-hour runs, and report package and
client versions and per-command pass/fail. Treat a stalled transfer that hits
its timeout as a VPN observation, not a tool bug.

## 6. Return funds and destroy

Only when the user asks. On a bench run: `recover RUN`, then `destroy RUN`.
Recover stops workers, then guards, then the heartbeat; closes channels;
returns the funds to the funding Safe; and audits the receipts. Destroy
re-checks recovery on-chain and deletes only this run's tagged VMs. Both ask
first. Check `status RUN` afterwards.

On a server the user names, do it by hand in this order:

1. Disconnect, then stop and mask the service: `gnosis_vpn-ctl stop-client; systemctl stop gnosisvpn; systemctl mask gnosisvpn`. A running node can re-fund its channels from the Safe while you drain it. Then stop the guards, and only then the heartbeat (section 0).
2. Check that the identity backup from section 4 exists.
3. Drain the Safe. Part of the wxHOPR sits in outgoing payment channels, not in the Safe. The identity's `chain_key` is the sole owner (threshold 1) of the Safe; each step is a Safe `execTransaction` sent from the node address, with the pre-validated signature `r = owner, s = 0, v = 1`:
   - `HoprChannels.initiateOutgoingChannelClosureSafe(node, dest)` for every OPEN channel (HoprChannels `0x860f50B24Ba6A862736c5045747323e03aa52Aa4` on jura).
   - After the notice period (300 s on jura; read it from the contract), `finalizeOutgoingChannelClosureSafe(node, dest)`, with a couple of blocks of margin after `closureTime`.
   - `wxHOPR.transfer(dest, safeBalance)`, then the node's leftover xDai minus about 0.002 for gas.
   - The keystore is scrypt + aes-128-ctr around JSON with `chain_key`; the password is in `.pass`. Sign with `cast mktx` and never echo the key. If the user has a sweep tool for this, prefer it.
   - Expect a little less back than was sent; the traffic is paid for.
4. Verify on-chain that the Safe holds 0 wxHOPR and the destination received the funds.
