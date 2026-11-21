# CI patch: UI-translation extraction report (issue #23)

The automation environment that produced branch `feature/ui-translations`
cannot push changes under `.github/workflows/` (the runner token is not
allowed to push workflow files), so the CI-side part of the task is
delivered here as a patch instead of a commit.

## What it does

Adds an **extraction report** step to the existing `lint-compile` job in
`.github/workflows/ci.yml` — the same checks `just gate` already runs
locally since this branch:

1. `python scripts/extract_translations.py --check` — re-extracts every
   `_()`/`_n()` string from `fluidvoice/` and fails when a shipped
   catalog (`po/sr-Latn.po`, `po/sr.po`, `po/bs.po`, …) is stale, holds
   an **orphaned msgid** (string removed from the code but still in the
   catalog), or has **untranslated** entries. On success it prints the
   per-language completeness report (the "extraction report in CI"
   acceptance criterion of acailic/SayItErmano#23).
2. `python scripts/build_translations.py --check` — fails when a
   committed `fluidvoice/locale/**/sayit-ermano.mo` does not match its
   `po/*.po` (prevents shipping stale binaries).

No new job, runner, or dependency is needed: both scripts are stdlib-only
and already run on the checkout (the `lint-compile` job installs nothing
extra for them).

## Patch

```diff
--- a/.github/workflows/ci.yml
+++ b/.github/workflows/ci.yml
@@ -121,6 +121,16 @@ jobs:
       - name: Request briefs carry a valid STATUS (same as `just gate`)
         run: python scripts/validate_requests.py
+
+      - name: UI translations extraction report (same as `just translations`)
+        # Issue #23: every user-facing string flows through gettext; the
+        # shipped catalogs (po/*.po) must cover the extraction output
+        # exactly — zero untranslated, zero orphaned msgids — and the
+        # committed .mo files must match their .po sources.
+        run: |
+          python scripts/extract_translations.py --check
+          python scripts/build_translations.py --check
       - name: Byte-compile everything
         run: |
           pip install -e . pytest
           python -m compileall -q fluidvoice tests
```

(Insertion point: after the `Request briefs carry a valid STATUS` step,
before `Byte-compile everything`.)

## Verification already done on the branch

- `python scripts/extract_translations.py --check` → exit 0:
  `sr-Latn: complete (501 strings)`, `sr: complete (501 strings)`,
  `bs: complete (501 strings)`, zero orphans.
- `python scripts/build_translations.py --check` → exit 0 (`.mo` fresh).
- `tests/test_i18n.py` (unit tier, runs in the existing `unit` job)
  additionally pins completeness/orphans/`.mo` freshness per push.
