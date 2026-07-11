# macOS operator companion

This setup runs the local companion as a per-user macOS LaunchAgent in
`existing` mode. It reads verified ResumeGenerator and Outreach evidence, binds
only to loopback, and cannot invoke the production pipeline. The upstream
nightly scheduler remains the sole production-run owner.

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
existing mode, disables live production execution, and rejects non-loopback
hosts.

## Validate and install

From this repository root:

```bash
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
inside macOS's protected Desktop folder. Re-run the installer after moving any
checkout.

The plist contains only an allowlisted set of paths, loopback settings, and
non-secret runtime values. Pairing codes and bearer tokens are never embedded;
they remain in the companion data directory with private file permissions.

## Inspect and pair

```bash
launchctl print "gui/$(id -u)/com.axepat.recruitingengine.operator-companion"
curl --fail --silent http://127.0.0.1:8765/api/v1/health
tail -f "$HOME/Library/Logs/RecruitingEngine/operator-companion.err.log"
```

To display or rotate the one-time pairing code in the same configured data
directory:

```bash
scripts/start-operator-companion.sh show-pairing
scripts/start-operator-companion.sh rotate-pairing
```

Treat the printed pairing code as a secret. These commands never place it in
the plist or service logs.

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
