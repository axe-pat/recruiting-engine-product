import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const exporter = await readFile(
  new URL("../scripts/export-static.mjs", import.meta.url),
  "utf8",
);
const probe = await readFile(
  new URL("../scripts/probe-operator-companion.sh", import.meta.url),
  "utf8",
);
const installer = await readFile(
  new URL("../scripts/install-operator-companion-launch-agent.sh", import.meta.url),
  "utf8",
);
const cockpitLauncher = await readFile(
  new URL("../scripts/open-operator-cockpit.sh", import.meta.url),
  "utf8",
);
const restartGuard = await readFile(
  new URL("../scripts/check-operator-restart-safety.py", import.meta.url),
  "utf8",
);
const companionApi = await readFile(
  new URL("../companion/recruiting_companion/api.py", import.meta.url),
  "utf8",
);

test("static export publishes an exact release compatibility marker", () => {
  assert.match(exporter, /productVersion = "1\.3\.0"/);
  assert.match(exporter, /compatibleCompanionVersion = "0\.3\.0"/);
  assert.match(exporter, /recruiting_engine\.static_compatibility/);
  assert.match(exporter, /release-compatibility\.json/);
  assert.match(exporter, /static-integrity\.json/);
  assert.match(exporter, /static-export\.staged/);
  assert.match(exporter, /publishValidatedStage/);
  assert.doesNotMatch(exporter, /new URL\("\.\.\/static-export\/"/);
  assert.match(companionApi, /STATIC_COMPATIBILITY_MARKER = "release-compatibility\.json"/);
  assert.match(companionApi, /STATIC_INTEGRITY_MARKER = "static-integrity\.json"/);
  assert.match(companionApi, /static_export_changed/);
  assert.match(companionApi, /companion_version != __version__/);
  assert.match(companionApi, /static_product_version/);
  assert.match(probe, /_validated_static_root/);
  assert.match(probe, /static export compatibility or integrity evidence is missing, unsafe, or incompatible/);
});

test("static generation promotes only while the old service is stopped and rolls back safely", () => {
  const exportValidate = exporter.indexOf("validateWithCompanion(outputRoot)");
  const exportPublish = exporter.indexOf("await publishValidatedStage()");
  assert.ok(exportValidate >= 0 && exportValidate < exportPublish);

  const bootout = installer.lastIndexOf('  /bin/launchctl bootout "${service}"');
  const releaseDatabase = installer.lastIndexOf("  release_legacy_database_gate\n");
  const promote = installer.lastIndexOf("promote_staged_static\n");
  const bootstrap = installer.indexOf('if ! /bin/launchctl bootstrap "${domain}" "${plist_path}"');
  assert.ok(bootout >= 0 && bootout < releaseDatabase);
  assert.ok(releaseDatabase < promote && promote < bootstrap);
  assert.match(installer, /validate_static_root "\$\{static_staged_root\}"/);
  assert.match(installer, /validate_static_root "\$\{static_live_root\}"/);
  assert.match(installer, /rollback_static_promotion/);
  assert.match(installer, /static_rollback_permitted=0/);
  assert.match(
    installer,
    /replacement service is still loaded; retaining its validated static export and rollback evidence/,
  );
});

test("installer holds the shared restart interlock through replacement and relies on RunAtLoad", () => {
  assert.match(installer, /--force-restart-active/);
  assert.match(installer, /check-operator-restart-safety\.py/);
  assert.match(installer, /--adapter-lock/);
  assert.match(installer, /start_restart_interlock/);
  const acquire = installer.lastIndexOf("  start_restart_interlock\n");
  const bootout = installer.lastIndexOf('  /bin/launchctl bootout "${service}"');
  const releaseDatabase = installer.lastIndexOf("  release_legacy_database_gate\n");
  const replace = installer.indexOf('mv "${temporary_plist}" "${plist_path}"');
  const bootstrap = installer.indexOf('if ! /bin/launchctl bootstrap "${domain}" "${plist_path}"');
  const enable = installer.indexOf('/bin/launchctl enable "${service}"');
  const release = installer.lastIndexOf("release_restart_interlock");
  assert.ok(acquire >= 0 && acquire < bootout);
  assert.ok(bootout < releaseDatabase && releaseDatabase < replace);
  assert.ok(replace < bootstrap && bootstrap < enable);
  assert.ok(enable < release, "exclusive interlock must survive through enable");
  assert.match(installer, /--require-database-gate/);
  assert.match(installer, /old-service-stopped/);
  assert.match(installer, /database-released/);
  assert.doesNotMatch(installer, /launchctl kickstart/);
  assert.match(installer, /launchctl bootstrap/);
});

test("cockpit launcher safely recovers only the installer-managed LaunchAgent", () => {
  assert.match(cockpitLauncher, /companion_is_healthy/);
  assert.match(cockpitLauncher, /Recruiting Engine Product operator companion installer/);
  assert.match(cockpitLauncher, /plutil -extract ManagedBy raw/);
  assert.match(cockpitLauncher, /plutil -extract Label raw/);
  assert.match(cockpitLauncher, /launchctl enable/);
  assert.match(cockpitLauncher, /launchctl bootstrap/);
  assert.match(cockpitLauncher, /launchctl kickstart/);
  assert.doesNotMatch(cockpitLauncher, /launchctl kickstart -k/);
  assert.doesNotMatch(cockpitLauncher, /rotate-pairing|repair-auth/);
});

test("restart guard quiesces legacy writers and selects no private job data", () => {
  assert.match(restartGuard, /fcntl\.LOCK_EX \| fcntl\.LOCK_NB/);
  assert.match(restartGuard, /\?mode=ro/);
  assert.match(restartGuard, /PRAGMA query_only = ON/);
  assert.match(restartGuard, /status IN \('queued', 'running'\)/);
  assert.match(restartGuard, /SELECT 1 FROM operator_jobs/);
  assert.match(restartGuard, /fcntl\.LOCK_EX \| fcntl\.LOCK_NB/);
  assert.match(restartGuard, /_write_ready_signal\(arguments\.ready_fifo, "ready"\)/);
  assert.match(restartGuard, /BEGIN IMMEDIATE/);
  assert.match(restartGuard, /old-service-stopped/);
  assert.match(restartGuard, /database-released/);
  assert.match(restartGuard, /final_signal != "release"/);
  assert.doesNotMatch(restartGuard, /SELECT \*/);
  assert.doesNotMatch(
    restartGuard,
    /parameters_json|reviewed_text|stdout_sha256|stderr_sha256/,
  );
});
