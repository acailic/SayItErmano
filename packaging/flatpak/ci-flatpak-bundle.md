# CI: build a `.flatpakbundle` artifact on tag (issue #11 — owner patch)

The task run that produced the shipping manifest
([`packaging/io.github.acailic.SayItErmano.yml`](../../io.github.acailic.SayItErmano.yml))
could not push workflow files (the runner token lacks that permission),
so the CI half of "CI builds a `.flatpakbundle` artifact on tag" is
this patch, to be applied by an owner with the usual workflow-rights
workflow. Apply with:

```bash
git apply packaging/flatpak/ci-flatpak-bundle.patch
# or: patch -p1 < packaging/flatpak/ci-flatpak-bundle.patch
```

## What the job does

- Triggers on `v*` tag pushes (release-publish creates those) and via
  manual `workflow_dispatch` for pre-submission test builds.
- Installs flatpak + the flathub remote, installs the pinned GNOME 48
  runtime/SDK pair the manifest declares, builds with `flatpak-builder
  --install-deps-from=flathub` from the COMMITTED manifest (the same
  file `tests/test_flatpak_manifest.py` pins), and packages a
  self-contained `io.github.acailic.SayItErmano-<tag>.flatpakbundle`
  with `flatpak build-bundle --runtime-repo=<flathub url>`.
- Uploads the bundle as a workflow artifact (retention 14 days);
  attaching it to the GitHub release stays a manual, verified step in
  `release-publish` — matching this repo's gates-first release
  philosophy (docs/dev/release-gates.md).
- Permissions are `contents: read` only; the tag is an input, never a
  write.

## Verification the owner should do on the first run

1. The build succeeds on the tag (whisper.cpp Vulkan + the four
   vendored wheels — this is the first real flatpak-builder run of the
   shipping manifest; the runner could only verify structure).
2. `flatpak install --from <bundle>` on a clean VM, then the
   press-key → speak → text row of the acceptance matrix
   (X11 / GNOME Wayland / Plasma Wayland).
3. `flatpak uninstall io.github.acailic.SayItErmano && rm -rf
   ~/.var/app/io.github.acailic.SayItErmano` leaves nothing behind.

## The patch

```diff
--- /dev/null
+++ b/.github/workflows/flatpak-bundle.yml
@@ -0,0 +1,49 @@
+name: flatpak-bundle
+
+# Issue #11: build a .flatpakbundle artifact on tag. The bundle is a
+# pre-submission verification artifact (install it on the VM rows of
+# the acceptance matrix); Flathub itself builds from the submitted
+# manifest. Attaching bundles to a release stays a manual step in
+# release-publish — nothing here publishes anywhere.
+on:
+  push:
+    tags: ["v*"]
+  workflow_dispatch:
+
+permissions:
+  contents: read
+
+concurrency:
+  group: flatpak-bundle-${{ github.ref }}
+  cancel-in-progress: true
+
+jobs:
+  bundle:
+    name: "flatpak-builder -> .flatpakbundle"
+    runs-on: ubuntu-24.04
+    timeout-minutes: 45
+    steps:
+      - uses: actions/checkout@v7
+
+      - name: Install flatpak + the flathub remote
+        run: |
+          set -euo pipefail
+          sudo apt-get update -qq
+          sudo apt-get install -y --no-install-recommends flatpak flatpak-builder
+          flatpak --user remote-add --if-not-exists flathub \
+              https://dl.flathub.org/repo/flathub.flatpakrepo
+
+      - name: Install the runtime/SDK the manifest pins
+        run: |
+          set -euo pipefail
+          flatpak --user install -y --noninteractive \
+              flathub org.gnome.Sdk//48 org.gnome.Platform//48
+
+      - name: Build from the committed manifest
+        run: |
+          set -euo pipefail
+          flatpak-builder --user --force-clean \
+              --repo=repo --install-deps-from=flathub \
+              build packaging/io.github.acailic.SayItErmano.yml
+
+      - name: Package the .flatpakbundle
+        run: |
+          set -euo pipefail
+          TAG="${GITHUB_REF_NAME#v}"
+          flatpak build-bundle \
+              --runtime-repo=https://dl.flathub.org/repo/ \
+              repo "io.github.acailic.SayItErmano-${TAG}.flatpakbundle" \
+              io.github.acailic.SayItErmano
+          sha256sum "io.github.acailic.SayItErmano-${TAG}.flatpakbundle" \
+              > "io.github.acailic.SayItErmano-${TAG}.flatpakbundle.sha256"
+
+      - uses: actions/upload-artifact@v4
+        with:
+          name: flatpakbundle-${{ github.ref_name }}
+          path: |
+            io.github.acailic.SayItErmano-*.flatpakbundle
+            io.github.acailic.SayItErmano-*.flatpakbundle.sha256
+          retention-days: 14
+          if-no-files-found: error
```
