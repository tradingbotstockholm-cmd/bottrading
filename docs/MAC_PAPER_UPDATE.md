# Existing Mac paper installation: prepare, activate, roll back

This is an operator procedure, not a claim of deployment. The actual Mac folder
and its private config/history are not accessible from the cloud. Migration and
preparation were tested against isolated legacy configs and SQLite WAL fixtures.
No live-market validation is established by this procedure.

## Before starting

1. Stop **all** scanners, dashboards, live runners, AI writers and scheduled jobs
   using `~/Downloads/solana_hunter_v4` or its databases. Use Control+C in their
   terminals; disable any launchd/cron restarts. Do not copy an actively changing
   project and call that a complete backup. The helper also makes SQLite API
   snapshots, but the full project/config backup requires stopped writers.
2. Keep real-money execution disabled. Do not run `run_live_mac.sh`, `hunter3
   run-live`, wallet funding, order or signing commands. The staged update writes
   `live.json.enabled=false` and creates the default and configured live kill
   files, even if the old live config had been enabled. The old project and its
   original configuration remain untouched until optional activation below.
3. Have Python 3.11+ and Git installed. Backups can contain credentials/private
   keys that already exist on your Mac: keep the backup folder local, private and
   outside every Git checkout. Never upload it to GitHub, chat or a support log.
4. Use the **full repair commit SHA reported in the PR/final handoff** as
   `EXPECTED_REPAIR_COMMIT`. Do not replace it with a moving branch name. This
   procedure intentionally does not embed a circular self-referencing commit SHA.

## Prepare a separate checkout; do not overwrite the old installation

Run in a fresh Terminal. Replace the one placeholder before proceeding:

```bash
set -euo pipefail
umask 077
EXPECTED_REPAIR_COMMIT='PASTE_FULL_REPAIR_COMMIT_SHA_HERE'
existing="$HOME/Downloads/solana_hunter_v4"
backup_root="$HOME/Downloads/solana_hunter_backups"
staged="$HOME/Downloads/solana_hunter_v4_repaired"

# Fails if the staged destination already exists; do not delete old state.
git clone --single-branch --branch fix/paper-execution-pipeline \
  https://github.com/tradingbotstockholm-cmd/bottrading.git "$staged"
test "$(git -C "$staged" rev-parse HEAD)" = "$EXPECTED_REPAIR_COMMIT"

# Build the validation environment outside both project trees so it never moves.
mkdir -p "$backup_root"
validation_dir="$(mktemp -d "$backup_root/validation.XXXXXXXX")"
python3 -m venv "$validation_dir/venv"
validation_python="$validation_dir/venv/bin/python"
"$validation_python" -m pip install -r "$staged/requirements.txt"
cd "$staged"
"$validation_python" -m hunter2.prepare_paper_update \
  --existing "$existing" --staged "$staged" --backup-root "$backup_root" \
  --expected-commit "$EXPECTED_REPAIR_COMMIT" --confirm-stopped

"$validation_python" -m unittest discover -s tests -v
"$validation_python" -m compileall -q hunter hunter2 hunter3 hunter4
"$validation_python" -m hunter2 status --config config.v4.json
```

The preparation helper:

- Refuses wrong revisions, modified tracked source, existing staged state,
  unprotected Git state paths, external ledger paths and symlinked data.
- Copies the **entire original project**, including code/config/research/virtualenv,
  into a unique private `paper-update-…/project` backup folder.
- Makes integrity-checked SQLite API snapshots, including committed WAL records.
  Staged ledgers use consolidated snapshots; only staged WAL/SHM copies are removed.
- Copies research/state and relevant configs to the fresh checkout. It never opens
  the original ledger for writing and never deletes or overwrites original history.
- Migrates only the recognized `assumed_slippage_pct: 0.01` versus
  `max_paper_slippage_pct: 0.0075` paper conflict (including an omitted ceiling
  defaulting to 0.0075). Base slippage becomes **0.0035**; the ceiling stays **0.0075**.
  Every other paper setting is preserved, including custom wallets, cash,
  liquidity, position limits, database path and risk settings.
- Saves the original staged config bytes in `.paper-config-backups/` before an
  atomic replacement. Other custom conflicts and invalid/non-paper configs fail
  closed. Paid streams must be explicitly disabled for this free paper workflow.
- Sets staged live execution off and creates live kill files. Preparation does
  **not** activate the new installation, start feeds or submit orders.

The helper prints a backup path and writes a private `manifest.json` there. Save
that location. If preparation fails, retain the partial backup and untouched old
project, diagnose the error, and use a new empty staging clone. For external or
symlinked databases, stop here and arrange an explicit backup/path plan; do not
rename or repoint the user ledger to force the helper to pass.

## Optional activation after successful local validation

Keep all old processes stopped. In the same shell, move the untouched original to
an additional rollback directory, then move the validated staging checkout into
its place. No database is overwritten in the old installation:

```bash
rollback_dir="$(mktemp -d "$backup_root/rollback.XXXXXXXX")"
mv "$existing" "$rollback_dir/original"
if ! mv "$staged" "$existing"; then
  mv "$rollback_dir/original" "$existing"
  exit 1
fi
printf 'Keep this rollback directory: %s\n' "$rollback_dir"
cd "$existing"
# A fresh project-local venv is built at its final path, not moved from staging.
bash setup_mac.sh
bash start_paper_mac.sh
```

`setup_mac.sh` and paper startup make additional WAL-aware ledger backups before
schema initialization. Existing configs are not replaced by examples. The first
V4 start may add schema tables without resetting fills or research history.

The dashboard must show the expected carried-over cash, positions, fills and
pause state. A heartbeat alone does not prove working feeds. Check Execution
Diagnostics and run `.venv/bin/python -m hunter2.live_probe` separately. Do not
claim deployment succeeded until the actual Mac checks pass; do not claim live
paper readiness until eligible observed-market signals produce audited fills.

## Rollback without discarding new history

1. Stop all processes and scheduled restarts in the updated installation.
2. Keep its entire directory, including any new fills, before restoring the old
   project. Do not copy old SQLite files over new history or merge two ledgers by
   hand. The renamed updated directory is the archive of post-update history.
3. In a Terminal, set `rollback_dir` to the saved path from activation:

```bash
set -euo pipefail
umask 077
existing="$HOME/Downloads/solana_hunter_v4"
backup_root="$HOME/Downloads/solana_hunter_backups"
rollback_dir='PASTE_THE_SAVED_ROLLBACK_DIRECTORY_HERE'
test -d "$rollback_dir/original"
failed_dir="$(mktemp -d "$backup_root/post-update.XXXXXXXX")"
mv "$existing" "$failed_dir/project"
if ! mv "$rollback_dir/original" "$existing"; then
  mv "$failed_dir/project" "$existing"
  exit 1
fi
```

The restored original preserves the pre-update SQLite history; post-update fills
remain in `failed_dir/project`. Its original `.venv` returns to its original path.
Do not run a live launcher after rollback. Keep/create `data/STOP_ALL_LIVE_TRADING`
and any custom live kill path before restarting **paper** only. If the original
`live.json` had `enabled=true`, back up that file privately and set only `enabled`
to `false` before restarting anything. The full original backup and consistent
SQLite snapshots remain available; restoring one requires all writers stopped
and separate preservation of the current database and sidecars.

## Standalone migration for an already-updated source tree

This changes config only; it never opens a ledger or live config:

```bash
.venv/bin/python -m hunter2.migrate_paper_config --paper-only --config config.v4.json
.venv/bin/python -m hunter2.migrate_paper_config --paper-only --config config.v4.json --apply
```

The first command is a dry-run. A compatible existing config is left byte-for-byte
unchanged, and repeated application does not create duplicate backups. A $100
liquidity floor remains a screening threshold: with the preserved 0.75% ceiling,
0.35% base impact model and $2 minimum order, at least $250 actual liquidity is
still needed for a minimum-size entry. No safeguard is disabled to force a fill.
