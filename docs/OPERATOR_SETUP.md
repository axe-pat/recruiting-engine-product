# macOS operator companion

This setup runs the local companion as a per-user macOS LaunchAgent in
`existing` mode. It reads verified ResumeGenerator and Outreach evidence, binds
only to loopback, and enables only fixed, hash-bound reviewed actions. Generic
live-run mode remains disabled. A reviewed production-nightly action invokes the
same attested scheduler wrapper with a fully fingerprinted off-cycle argument
set loaded from ResumeGenerator's canonical `nightly_contract.py print` surface.
Its reviewed artifact binds the contract script and exact returned argv, including
bounded app-queue delivery and Track 2 LinkedIn delivery; it is not a
preparation-only run.

The installed companion is also the primary web server for this single-user
operator product. Its canonical URL is `http://127.0.0.1:8765/app/`. Because the
generated UI and API share one loopback origin, it uses a persistent HttpOnly
local cookie and does not require repeated pairing tokens. Hosted pairing is
retained for the later public/portable path.

## Defaults

The scripts resolve absolute paths from their own installed repository location:

| Setting | Default |
|---|---|
| ResumeGenerator | sibling `ResumeGenerator v1/` |
| Outreach | sibling `Outreach/` |
| Runtime locks | `~/Library/Application Support/ResumeGenerator/` |
| Production attestation | `~/Library/Application Support/ResumeGenerator/production_release.json` |
| Companion data | `~/.recruiting-engine-companion/` |
| Bind | `127.0.0.1:8765` |
| Logs | `~/Library/Logs/RecruitingEngine/` |
| LaunchAgent | `com.axepat.recruitingengine.operator-companion` |

Override paths before probing or installing with
`RECRUITING_ENGINE_RESUME_ROOT`, `RECRUITING_ENGINE_OUTREACH_ROOT`,
`RECRUITING_ENGINE_RUNTIME_DIR`, `RECRUITING_ENGINE_ATTESTATION_PATH`,
`RECRUITING_ENGINE_RESUME_PYTHON`, `RECRUITING_ENGINE_OUTREACH_PYTHON`,
`RECRUITING_ENGINE_COMPANION_PYTHON`, or `RECRUITING_ENGINE_DATA_DIR`.
Overrides must be absolute. `RECRUITING_ENGINE_PORT` and
`RECRUITING_ENGINE_HOSTED_ORIGIN` are also supported. The start script forces
existing mode, sets `RECRUITING_ENGINE_ALLOW_LIVE_RUNS=0`, explicitly enables
the narrower `RECRUITING_ENGINE_ALLOW_REVIEWED_ACTIONS=1`, and rejects
non-loopback hosts.

## Validate and install

From this repository root:

```bash
npm install
npm run export:static
scripts/probe-operator-companion.sh --production-preflight
scripts/start-operator-companion.sh --dry-run
scripts/install-operator-companion-launch-agent.sh --dry-run
scripts/install-operator-companion-launch-agent.sh --production-preflight
```

The installer writes one plist under `~/Library/LaunchAgents/`, creates the log
directory, loads the service, and starts it. Absolute paths are resolved when
the plist is generated. The LaunchAgent executes a supported system/Homebrew
Python directly from the home directory while `PYTHONPATH` points at the live
product checkout. This avoids asking `launchd` to execute a script or virtualenv
inside macOS's protected Desktop folder. `npm run export:static` builds and
validates `static-export.staged/` without touching the served `static-export/`.
The probe prefers that pending generation when present. The installer promotes
it only after the old service stops, validates the promoted tree, and restores
the previous generation if replacement startup fails. Its required
`release-compatibility.json` binds product `1.3.0` to companion `0.3.0`, while
`static-integrity.json` binds every served path, size, and SHA-256. The probe,
installer, startup validation, and per-request read all fail closed on missing,
unsafe, mismatched, or changed evidence. Re-run the installer after moving any
checkout or rebuilding the UI.

Before booting out an installed service, the installer first performs a
mutation-free check, then acquires the shared adapter-mutation lock exclusively.
While holding that interlock it also opens a SQLite `BEGIN IMMEDIATE` writer gate
and rechecks the scheduler and pipeline advisory locks plus queued/running
companion jobs. The writer gate prevents a pre-0.3 companion, which predates the
shared admission lock, from inserting a job between the check and shutdown. Only
after `bootout` succeeds does the installer release the SQLite transaction and
acknowledge that release before starting the new process. The adapter lock stays
exclusive through plist replacement, bootstrap, and enable. New companion job
admission takes a shared lock around queue insertion, while a new scheduler waits
on the exclusive lock and continues after release. Private FIFO handshakes keep
both phases synchronized without credentials and fail closed on bounded timeouts.
`--force-restart-active` is an explicit emergency override and must never be
used merely to hurry a live run; normal upgrades wait for terminal evidence.

The plist contains only an allowlisted set of paths, loopback settings, and
non-secret runtime values. Pairing codes and bearer tokens are never embedded;
they remain in the companion data directory with private file permissions.

## Inspect and connect

```bash
launchctl print "gui/$(id -u)/com.axepat.recruitingengine.operator-companion"
curl --fail --silent http://127.0.0.1:8765/api/v1/health
tail -f "$HOME/Library/Logs/RecruitingEngine/operator-companion.err.log"
```

For the normal local workflow, open:

```bash
scripts/open-operator-cockpit.sh
```

The launcher validates the mode-`0600` private bearer against hash-only auth
state, creates a hash-only single-use activation ticket with a two-minute
maximum lifetime, captures it without printing it, and opens a same-origin URL
whose fragment is removed from history before exchange. Raw HTML never mints a
privileged cookie. Successful activation establishes a host-only, one-year,
`HttpOnly`, `SameSite=Strict` cookie that remains usable across normal browser
and service restarts. Do not rotate pairing material to repair this path;
pairing is not part of local UI startup.

If activation reports inconsistent bearer/state, run the explicit repair only:

```bash
PYTHONPATH="$PWD/companion" python3 -m recruiting_companion repair-auth
```

Repair refuses healthy state. When needed, it invalidates bearer, pairing, web,
cookie, and activation sessions and reports only auth file paths, never new
secrets. Run the launcher again afterward.

Only for the hosted site or a new extension client, display or rotate the
one-time pairing code in the same configured data directory:

```bash
scripts/start-operator-companion.sh show-pairing
scripts/start-operator-companion.sh rotate-pairing
```

Treat the printed pairing code as a secret. These commands never place it in
the plist or service logs.

A pairing code is consumed once. After a successful exchange, the hosted tab
uses a 12-hour, tab-scoped `re_web_` session and Settings shows **Connected**.
Do not paste the same `re_pair_` value again; disconnect the hosted tab and
rotate the pairing code only when a new hosted session is actually needed.

## Daily operator runbook

1. Run `scripts/open-operator-cockpit.sh` (or open the canonical local URL when
   this browser already has its cookie) and check the header connection state.
2. Check the run-progress card before triggering anything. The UI polls exact
   scheduled and cockpit progress while an active scheduler/pipeline attempt or
   operator job exists. If one is active, follow it instead of starting a
   duplicate.
3. Use `/app/plan` for the evidence-grounded next-run action plan. While a run is
   active it remains explicitly provisional and based on the prior verified run.
4. Use `/app/accounts` for the safe aggregate and bounded current action queue.
   **Open account tracker** uses the server-owned allowlisted workbook path and a
   fixed confirmation to open the complete workbook in Excel.
5. **Run E2E** starts only the reviewed production nightly contract. It can
   perform bounded application-queue and Track 2 LinkedIn delivery; email stays
   separately recipient-reviewed. Review the exact target and delivery contract,
   approve it, then enter `RUN_REVIEWED_NIGHTLY`.
6. Keep the page open if convenient, but the run is local and does not depend on
   the browser tab. After terminal state, verify the immutable run report rather
   than a mutable `latest` alias.

The upstream nightly owns its dedicated LinkedIn browser through a private
per-run marker and closes only that owned browser during terminal cleanup. The
progress projection never treats an arbitrary visible Chrome window as run
evidence. Do not kill or restart the companion or run-owned browser while an
active job is in progress.

See [PRIMARY_LOCAL_UI.md](PRIMARY_LOCAL_UI.md) for the complete operating,
troubleshooting, and external-agent handoff contract.

The plist on disk and `launchctl print` describe different things: the former is
the next load configuration, while the latter is the currently loaded job.
Inspect both when troubleshooting a stale service.

Because the default checkouts live under Desktop, macOS privacy controls can
still allow an interactive probe while denying a background interpreter access
to repository data. The default direct-Python launcher avoids the common
protected-script failure. If the stderr log still reports `Operation not
permitted`, either move the checkouts outside a protected folder or grant the
plist's exact Python executable the required macOS file access; Terminal
permission alone does not authorize `launchd`.

## Uninstall

```bash
scripts/uninstall-operator-companion-launch-agent.sh --dry-run
scripts/uninstall-operator-companion-launch-agent.sh
```

Uninstall stops and removes only the managed LaunchAgent. Companion data,
pairing state, and logs are retained, so reinstalling is reversible.
