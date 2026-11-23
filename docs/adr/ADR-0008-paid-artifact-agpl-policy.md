# ADR-0008: Paid-artifact AGPL policy — offer the portal wheel's source (A) or drop the portal extra (B)

Date: 2026-11-23
Status: **Proposed — decision deferred to OPERATOR.** Both options are
implemented, tested and rehearsed behind one switch
(`PAID_ARTIFACT=1`, option via `PAID_ARTIFACT_OPTION=A|B`); the code
default (A) is this ADR's *recommendation*, not a decision. The
operator marks the chosen option here — flipping the launch posture is
then one environment variable, no code.

## Context

The rights and redistribution inventory
([2026-11-22-rights-and-redistribution-inventory.md](../research/2026-11-22-rights-and-redistribution-inventory.md),
§2.1 "The AGPL finding") established the one real copyleft wrinkle in
an otherwise GPL-clean tree:

- the wheel vendored at `packaging/wheels/global_shortcut_portal-0.1.0-py3-none-any.whl`
  declares `License-Expression: AGPL-3.0-only` in its METADATA;
- it is an optional `portal` extra on host installs
  ([pyproject.toml](../../pyproject.toml)) but is **installed
  unconditionally in the Flatpak** (both manifests' `deps` module);
- GPLv3 §13 permits combining and conveying the GPL work with an
  AGPLv3 work, so the *free* Flatpak is fine — but a **paid** artifact
  conveying the combination must also offer that wheel's Corresponding
  Source (AGPLv3 §6; the combination is governed by both licenses);
- the deb never carries the wheel (no pin in
  [packaging/deb/constraints.txt](../../packaging/deb/constraints.txt)),
  and since v0.8.2 the deb is the primary channel
  ([ADR-0005](ADR-0005-commercial-sku.md) sells exactly that build).

Per [ADR-0005](ADR-0005-commercial-sku.md), the paid SKU is a
convenience-and-support bundle: no core feature is paid-only, and the
offer must stay honest under §10 (no added restrictions on granted
rights). Whatever we do mechanically must hold that line.

No paid artifact exists yet. This ADR prepares both remediation paths
so the operator's choice is a one-line decision, tested now.

## Decision

**OPERATOR — the choice between A and B is not made by this ADR.**
What IS decided (engineering, both options verified by
[tests/test_paid_policy.py](../../tests/test_paid_policy.py)):

- one switch: `PAID_ARTIFACT=1` (env var, default off — free builds are
  byte-for-byte unaffected);
- one selector: `PAID_ARTIFACT_OPTION=A|B` (default `A`, the
  recommendation below);
- the machinery lives in
  [packaging/paid_policy.py](../../packaging/paid_policy.py) and is
  consumed by `packaging/build-deb.sh` and the Flatpak manifest
  rehearsal below.

### Option A — paid artifacts bundle/offer the AGPL wheel's source

The paid artifact keeps the full dependency set (functional parity with
the free build) and satisfies the AGPL offer duty mechanically:

- **In-artifact offer (both channels):** the wheel's own license text
  and its sources are extracted from the vendored wheel into the
  artifact —
  - deb: `/usr/share/doc/sayit-ermano/global-shortcut-portal/`
    (`LICENSE`, `src/`, and a `SOURCE` notice); the pure-Python
    wheel's `.py` files are the preferred form for modification;
  - Flatpak: a generated `agpl-source-offer` module copies the
    installed sources to `/app/share/doc/global-shortcut-portal/src`
    and the license to the Flathub-required
    `share/licenses/$FLATPAK_ID/agpl-source-offer/`.
- **Source tarball URL next to the artifact:** the pinned sdist of the
  exact vendored version — `global_shortcut_portal-0.1.0.tar.gz`,
  sha256 `bae2d6ba…c22aebda`, URL recorded in
  `packaging/paid_policy.py` (`AGPL_SDIST_URL`, verified against the
  PyPI JSON API 2026-11-23; the upstream codeberg repo has no `v0.1.0`
  tag, oldest is `v0.2.0`, so the PyPI sdist is the only exact
  Corresponding-Source tarball) — is published as a release asset / an
  apt-repo entry adjacent to the paid download, at no further charge.
- **What "offer" means per channel:**
  - **apt/deb channel:** the deb itself carries license + sources
    (above), and the sale page / release notes link the sdist URL.
    Since the deb's closure does not even include the wheel, the
    in-deb material is belt-and-braces documentation of where the
    combination's source lives; the binding GPL §6d Corresponding
    Source for our own code remains the public repo + release tag.
  - **Flathub channel:** the manifest *is* the offer — every input is
    content-pinned in the submitted manifest (wheel `url`+`sha256`),
    and the generated module ships sources + license inside `/app`.
    AGPL §13's network-interaction terms stay inert: the app has no
    network listener ([ADR-0001](ADR-0001-no-tcp.md)).
- **Rehearsal (deb):**
  `PAID_ARTIFACT=1 PAID_ARTIFACT_OPTION=A packaging/build-deb.sh`
  (inside the pinned container:
  `docker run --rm -e PAID_ARTIFACT=1 -e PAID_ARTIFACT_OPTION=A -v "$PWD/dist:/out" sayit-ermano-deb`).
- **Rehearsal (Flatpak bundle):**
  `PAID_ARTIFACT=1 PAID_ARTIFACT_OPTION=A python3 packaging/paid_policy.py flatpak packaging/io.github.acailic.SayItErmano.yml > /tmp/paid.yml && flatpak-builder --force-clean build/paid /tmp/paid.yml`
  (same for the flathub variant's path).

### Option B — paid builds exclude the `portal` extra

The paid dependency set drops `global-shortcut-portal` entirely, so no
AGPL code is conveyed and no offer duty attaches:

- **deb:** `PAID_ARTIFACT_OPTION=B` filters the AGPL pin out of the
  installed lock (`paid_policy.py filter-lock`). Today this is a
  verified no-op — the deb lock never carried the wheel — and the
  filter's standing value is the guarantee that it never appears in a
  paid deb without the switch being seen.
- **Flatpak:** `paid_policy.py flatpak` emits a manifest whose `deps`
  module installs only the four non-AGPL wheels (six, python-xlib,
  pillow, dbus-next), with the wheel's license-install line and source
  item removed.
- **What functionality is lost, where:** only the in-sandbox
  **GlobalShortcuts portal hotkey** (`hotkey.wayland_portal`,
  `fluidvoice/portal_hotkey.py`) — the import probe in
  `fluidvoice/session.py` reports it unavailable and the documented
  fallback ladder
  ([ADR-0006](ADR-0006-flatpak-input.md),
  [Wayland guide](../guides/wayland.md)) takes over:
  - GNOME 48+ / Plasma 5.27+ / Hyprland (the desktops whose portal
    backends make the hotkey work today): DE custom shortcut running
    the generated `sayit-ermano-toggle` script (Settings → Wayland
    prints the per-DE steps; `sayit-ermano doctor` reports the active
    rung) — the CLI-toggle fallback ADR-0006 keeps first-class.
  - sway / COSMIC: nothing lost — they have no portal backend and
    already live on the compositor-bind fallback.
  - **EIS text insertion is NOT affected**: `fluidvoice/portal_eis.py`
    speaks D-Bus via `dbus-next` (MIT, stays vendored); only the
    hotkey path imports the AGPL wheel.
  - Host installs (pipx/AUR/deb) are unaffected either way — the
    extra stays optional there for anyone, free.
- **Rehearsal (deb):** `PAID_ARTIFACT=1 PAID_ARTIFACT_OPTION=B packaging/build-deb.sh`.
- **Rehearsal (Flatpak bundle):**
  `PAID_ARTIFACT=1 PAID_ARTIFACT_OPTION=B python3 packaging/paid_policy.py flatpak packaging/io.github.acailic.SayItErmano.yml > /tmp/paid.yml && flatpak-builder --force-clean build/paid /tmp/paid.yml`.

### Recommendation (with trade-offs) — not a decision

**Recommend A** as the launch default, for three reasons:

1. **Parity honesty (decisive).** ADR-0005 promises the paid SKU adds
   convenience, never removes function. Under B, a paid *Flatpak*
   would do strictly less than the free Flatpak on GNOME/Plasma — the
   inverse of a paid-only feature, but the same honesty seam. A keeps
   free and paid functionally identical.
2. **Cost is one build step, already paid.** The offer is extracted
   from bytes already in the tree; the sdist URL is pinned; the
   ongoing duty is keeping one release asset/link adjacent to the paid
   download.
3. **The deb — today's only paid artifact — needs neither.** Its
   closure excludes the wheel; under A the deb merely ships
   documentation, under B the filter is a no-op guard. The choice only
   bites the day a paid Flatpak exists, and A is the only option that
   keeps it whole.

Trade-offs of A: the paid artifact conveys a GPL+AGPL combined work —
that part can never be relicensed or offered under different terms
(already true of the free Flatpak); the adjacent-source duty must be
remembered at every paid release (mitigated: the build fails loudly if
the vendored wheel lacks license/source, and the ADR documents the
sdist pin). Trade-offs of B: single-license story for the paid channel
and zero offer bookkeeping, at the price of the portal hotkey on
GNOME/Plasma/Hyprland paid builds and a free/paid capability gap that
support must explain.

## Consequences

- `packaging/paid_policy.py` is the single implementation of both
  paths; `build-deb.sh` and the flatpak rehearsal consume it, and the
  unit tier pins both branches offline (fakes for lock/manifest shape,
  the real vendored wheel for the offer bytes) —
  `python3 scripts/test_tier.py unit -q tests/test_paid_policy.py`.
- Free builds never set `PAID_ARTIFACT`; the committed manifests and
  lock are untouched by this ADR's machinery (the paid flatpak manifest
  is *generated*, never committed).
- When the operator decides, they mark the choice here (and, if B,
  whether it applies to every channel or deb-only), set the launch
  pipeline's `PAID_ARTIFACT_OPTION`, and re-run the rehearsal commands
  above; no code change is expected.
- A wheel version bump re-pins `AGPL_SDIST_URL`/`AGPL_SDIST_SHA256`
  and the manifests' sha256 entries in the same commit
  (tests catch drift: the offer notice carries the wheel's own sha256,
  and the manifest suite pins the vendored hashes).
- Revisit triggers: a paid Flatpak shipping at all (re-evaluate B's
  parity cost with real users), upstream tagging the vendored version
  (switch the offer target to the codeberg tarball), or counsel input
  from the rights note's OPEN-5 row changing the §6/§13 analysis.
