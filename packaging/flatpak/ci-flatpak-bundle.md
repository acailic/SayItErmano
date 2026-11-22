# CI patch: build a `.flatpakbundle` artifact on tag (owner action)

The automation that implements issue #11 cannot push workflow files
(the token lacks `workflows:` permission), so the CI half of "CI
produces the bundle on tag" ships here as a patch. **This file is not
wired into CI** — a human applies the diff on `linux`:

```sh
git apply packaging/flatpak/ci-flatpak-bundle.md  # not a .diff file — see below
```

The diff is embedded in this markdown; simplest is to copy the
workflow block out of the diff by hand into
`.github/workflows/flatpak-bundle.yml` and commit.

## What it adds

`.github/workflows/flatpak-bundle.yml` — builds the SHIPPING manifest
(`packaging/io.github.acailic.SayItErmano.yml`) with flatpak-builder on
`ubuntu-24.04`, exports a single-file `.flatpakbundle` plus its
sha256, uploads both as the `flatpakbundle` artifact. Triggers:

- `push` on tags `v*` — the task's acceptance row ("CI produces the
  bundle on tag");
- `workflow_dispatch` — the repo's human-dispatch habit (release-*
  workflows), so the lane is runnable before a tag exists.

Trigger precedent: `pages.yml` already runs on pushes to `linux`
(paths-filtered); release workflows stay manual. A tag-only push
trigger adds no branch churn.

## Notes for the first run

- The manifest's app module builds from
  `https://github.com/acailic/SayItErmano.git` pinned to the release
  tag (`tag: v0.9.0`, bumped with every release alongside the
  metainfo `<release>`; `tests/test_flatpak_manifest.py` pins the
  parity with the pyproject version). On a release tag the tag IS the
  tip of `linux` (release-prepare pushes the bump commit to `linux`
  before tagging), so the CI bundle always matches the release. The
  Flathub-shaped copy of the manifest
  (`packaging/flatpak/io.github.acailic.SayItErmano.flathub.yml`)
  pins the same tag — the workflow needs no change for either.
- First run downloads the GNOME 51 Sdk/Platform (~1 GB); later runs
  benefit from a cache if you add `flatpak-builder` cache steps
  (left out to keep the patch minimal).
- Wheels for the `deps` module are committed under
  `packaging/wheels/` — no network needed inside flatpak-builder's
  build sandbox.

## The diff

```diff
--- /dev/null
+++ b/.github/workflows/flatpak-bundle.yml
@@ -0,0 +1,48 @@
+name: flatpak-bundle
+
+# Flathub funnel, CI half of #11: build the shipping manifest and
+# export a single-file .flatpakbundle on every release tag (plus
+# manual dispatch, the release-* habit). The Flathub submission
+# itself (#12) builds from this same manifest in the flathub repo.
+on:
+  push:
+    tags: ["v*"]
+  workflow_dispatch:
+
+permissions:
+  contents: read
+
+concurrency:
+  group: flatpak-bundle
+  cancel-in-progress: false
+
+jobs:
+  bundle:
+    name: "flatpak-builder -> .flatpakbundle"
+    runs-on: ubuntu-24.04
+    timeout-minutes: 45
+    steps:
+      - uses: actions/checkout@v7
+
+      - name: Install flatpak-builder + GNOME 51 runtime
+        run: |
+          set -euo pipefail
+          sudo apt-get update -qq
+          sudo apt-get install -y flatpak flatpak-builder
+          flatpak --user remote-add --if-not-exists flathub \
+              https://flathub.org/repo/flathub.flatpakrepo
+          flatpak --user install -y --noninteractive \
+              org.gnome.Sdk//51 org.gnome.Platform//51
+
+      - name: Build the manifest into a local repo
+        run: |
+          set -euo pipefail
+          flatpak-builder --user --install-deps-from=flathub \
+              --force-clean --repo=bundle-repo \
+              .flatpak-build packaging/io.github.acailic.SayItErmano.yml
+
+      - name: Export the single-file bundle + digest
+        run: |
+          set -euo pipefail
+          VERSION="${GITHUB_REF_NAME:-manual}"
+          flatpak build-bundle bundle-repo \
+              "SayItErmano-${VERSION}.flatpakbundle" \
+              io.github.acailic.SayItErmano
+          sha256sum ./*.flatpakbundle > flatpakbundle.sha256
+          cat flatpakbundle.sha256 >> "$GITHUB_STEP_SUMMARY"
+
+      - uses: actions/upload-artifact@v7
+        with:
+          name: flatpakbundle
+          path: |
+            *.flatpakbundle
+            flatpakbundle.sha256
+          if-no-files-found: error
```
