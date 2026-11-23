# Recovery runbook — where your data lives, how to back it up, how to come back

For when something went wrong: config won't parse, history looks
damaged, an upgrade surprised you, or you are moving machines. Every
claim below is grounded in the code paths cited — nothing invented.
First stop for "is my setup healthy?" is always
`sayit-ermano doctor` (fluidvoice/doctor.py — it never prints your
dictated content, only paths, counts and states; see the
[redaction audit](../quality/redaction-audit.md)).

## Where everything lives

All paths resolve in `fluidvoice/paths.py` (XDG, with a one-time
takeover of the pre-rename `~/.config/fluidvoice` /
`~/.local/share/fluidvoice` dirs — paths.py:18-31 — so a rename-era
install keeps its data):

| What | Path | Restorable? |
|---|---|---|
| Config | `~/.config/sayit-ermano/config.toml` (env override: `SAYITERMANO_CONFIG`) | yes — defaults regenerate; see below |
| **History** (your transcripts) | `~/.local/share/sayit-ermano/history.jsonl` | **only from your backups** |
| Retained dictation audio | `~/.local/share/sayit-ermano/audio/` (only if you enabled `history.save_audio`) | no — but it is re-recordable by nature |
| AI provider profiles | `~/.config/sayit-ermano/ai-profiles.json` | from backup (hand-written) |
| Prompt profiles | `~/.config/sayit-ermano/prompt-profiles.json` | from backup |
| Dictionary-learning decisions | `~/.config/sayit-ermano/dictionary-suggestions.json` | regrows with use |
| Imported license key | `~/.config/sayit-ermano/license.key` (mode 0600) | re-import with `sayit-ermano license --key …` |
| Downloaded speech/chat models | `~/.cache/sayit-ermano/models/` | yes — one click in Settings → Models re-downloads |
| Machine-local state (update check, portal token, autostart intent, mic baselines) | `~/.config/sayit-ermano/{update-state,portal-state,autostart-state,mic-baseline}.json` | regenerates; not worth backing up |
| The one-shot-installed runtime | `~/.local/share/sayit-ermano/venv/` + `~/.local/bin/sayit-ermano` | yes — re-run the installer |
| Control socket | `$XDG_RUNTIME_DIR/sayit-ermano.sock` | transient |

The rule of thumb: **back up `~/.config/sayit-ermano/` and
`~/.local/share/sayit-ermano/` minus the `venv/` subdir** — that is
100% of your settings and history.

## Back up / restore

```sh
# back up everything that matters (stop the daemon first so history is quiet)
systemctl --user stop sayit-ermano   # or: pkill -f 'python -m fluidvoice daemon'
tar czf sayit-backup.tar.gz -C ~ \
    .config/sayit-ermano \
    .local/share/sayit-ermano --exclude .local/share/sayit-ermano/venv

# restore (daemon still stopped)
tar xzf sayit-backup.tar.gz -C ~
systemctl --user start sayit-ermano
```

History alone can also be exported from the app (History window →
**Export as ZIP** — entries plus any retained audio) or the CLI:

```sh
sayit-ermano history --export sayit-history.zip
```

## Corrupted config

What actually happens (verified in code): `load_config`
(fluidvoice/config.py:1250-1258) parses the file with `tomllib` and
**raises `TOMLDecodeError` on invalid TOML** — it does not fall back
silently. Consequences:

- `sayit-ermano daemon` exits at startup with that error
  (fluidvoice/cli.py:167-181).
- `sayit-ermano doctor` still runs: it catches the failure and reports
  with defaults (fluidvoice/doctor.py:745-748).
- The GTK app/settings loads through the same `load_config`.

Recovery:

```sh
mv ~/.config/sayit-ermano/config.toml ~/.config/sayit-ermano/config.toml.broken
sayit-ermano config init     # writes the full commented template (config.py:1343)
```

Then either re-set your keys in Settings, or fix and restore the
`.broken` file. Caution: the settings UI saves only whitelisted keys
and carries the rest (like `ai.api_key`) over from the *existing* file
(fluidvoice/config.py:1356-1372) — if that file is unparseable, the
carry-over is empty and a save would drop the un-carried keys. So
**restore from your backup rather than editing through a broken
config**. Note that manual saves are safe on a *valid* file: writes are
atomic (temp file + rename, mode 0600 — config.py:1327-1340).

## Corrupted history

History is append-only JSONL with crash-safe transactions (flock
sidecar `history.jsonl.lock`, unique temp files, fsync + atomic rename
— fluidvoice/history.py module docstring). Verified behavior on damage:

- A crash can never leave a half-written row — at worst a stale
  `*.tmp` sidecar, which the next exclusive transaction removes.
- A torn byte or damaged line does not crash or hide the other rows:
  every reader decodes with `errors="replace"` and silently skips lines
  that fail JSON parsing (history.py:283-295; same for `tail`,
  history.py:297-313).
- The store caps itself at 5000 entries, keeping the newest
  (`MAX_ENTRIES`, history.py:35).

So a "corrupted" history usually means a few lost rows, not a lost
file. To repair:

```sh
# 1. stop the daemon, then back up what's there
cp ~/.local/share/sayit-ermano/history.jsonl history.jsonl.bak-manual

# 2. drop the damaged lines (any JSON-checker works); e.g.:
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path.home() / ".local/share/sayit-ermano/history.jsonl"
def ok(line):
    try:
        json.loads(line)
        return True
    except ValueError:
        return False
kept = [l for l in p.read_text(errors="replace").splitlines()
        if l.strip() and ok(l)]
p.write_text("\n".join(kept) + "\n")
EOF
```

(The sketch above keeps only parseable lines — exactly what the app's
own readers do, history.py:292-294.) Two in-app repair paths also write
automatic backups before rewriting: `sayit-ermano history --scrub-tests`
(leaves `history.jsonl.bak-<timestamp>` first — history.py:592-628) and
edits/deletes from the History window, which run as locked
read-modify-replace transactions (history.py:343-354).

## Upgrades: what is preserved

The upgrade rehearsal evidence lives in
[docs/quality/upgrade/](../quality/upgrade/README.md) — a real
install → update → rollback cycle through the one-shot installer
(latest run: [2026-11-22](../quality/upgrade/2026-11-22.md), PASS),
plus the offline gates in `tests/test_q9_soak_upgrade.py`.

What an update touches and doesn't (scripts/install-one-shot.sh:26,
102-104, 249-250): it stops the daemon, does `rm -rf` **only on
`~/.local/share/sayit-ermano/venv`**, and copies the new venv in place.
`~/.config` and everything else under `~/.local/share/sayit-ermano`
(history, audio) are never touched — the rehearsal verifies config and
history come out **byte-identical** (sha256) across update *and*
rollback. Downloaded models live under `~/.cache` and are equally
untouched; worst case they re-download from Settings → Models.

Rollback is the same command with the older payload:
`install-one-shot.sh --update <older-deb>` (verified in the rehearsal:
state byte-identical again).

## Moving to a new machine

1. Back up (above) on the old machine.
2. Install on the new machine (any route from
   [install.md](install.md)).
3. Stop the daemon, restore the two directories, start the daemon.
4. `sayit-ermano doctor` — models will show "not downloaded"; fetch
   them again in Settings → Models (they are cache, deliberately not
   migrated).

## When transcripts appear where you don't expect them

They shouldn't: by default no diagnostic surface (logs, `doctor`,
notifications, error text) carries dictated content — see the
[redaction audit](../quality/redaction-audit.md). The files that
legitimately hold content are the ones in the table above.
