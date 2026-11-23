# ADR-0008: Paid artifacts and the AGPL-3.0-only `global-shortcut-portal` wheel

Date: 2026-11-23
Status: **Proposed — decision reserved to the OPERATOR** (both options are
implemented behind one switch; see [Decision](#decision))

## Context

The rights inventory
([2026-11-22-rights-and-redistribution-inventory.md](../research/2026-11-22-rights-and-redistribution-inventory.md),
"The AGPL finding") established the one real copyleft wrinkle in the
shipped artifacts:

- the wheel vendored at
  `packaging/wheels/global_shortcut_portal-0.1.0-py3-none-any.whl`
  declares `License-Expression: AGPL-3.0-only` and is installed
  unconditionally in the Flatpak
  ([packaging/io.github.acailic.SayItErmano.yml](../../packaging/io.github.acailic.SayItErmano.yml),
  `deps` module);
- GPLv3 §13 permits combining and conveying the GPL work with an
  AGPLv3 work, so the combination itself is fine — but a **paid**
  conveyance of that combination is a GPL+AGPL combined work whose
  Corresponding Source must include the AGPL wheel's source;
- the **deb never includes the wheel** (no pin in
  [packaging/deb/constraints.txt](../../packaging/deb/constraints.txt));
  on the host the `portal` extra is optional
  ([pyproject.toml](../../pyproject.toml));
- no paid artifact exists yet ([ADR-0005](ADR-0005-commercial-sku.md)
  is Proposed; since v0.8.2 the deb is the primary channel and the
  Flatpak is an owner-pending submission runbook), so this is a
  planning constraint, not an incident.

Scope note: only `global-shortcut-portal` is AGPL. `dbus-next` (MIT)
rides in the same pyproject `portal` extra but is imported by EIS
insertion (`fluidvoice/portal_eis.py`) independently of the hotkey —
it stays in every option below.

## Option A — paid artifacts bundle the wheel's Corresponding Source

The paid artifact keeps the portal hotkey AND carries the AGPL
obligation inside itself.

Mechanics (implemented, `packaging/paidartifact.py`):

- the wheel's sdist is vendored next to it:
  `packaging/wheels/global_shortcut_portal-0.1.0.tar.gz`
  (sha256 `bae2d6ba…c22aebda`, the PyPI-published source for the exact
  pinned wheel version).
- **deb** — `PAID_ARTIFACT=1 PAID_AGPL_POLICY=source
  packaging/build-deb.sh`: the vendored wheel is hash-verified, pip
  installs it `--no-deps` into the bundled venv (portal hotkey arrives
  on the host install), and the sdist + the wheel's LICENSE text are
  staged into `/usr/share/licenses/sayit-ermano/global-shortcut-portal/`.
- **Flatpak** — `python3 packaging/paidartifact.py flatpak <manifest>`
  appends the sdist as a `deps` source (local `path:` in the CI
  variant, PyPI `url:` + sha256 in the Flathub variant) plus an install
  line into
  `$FLATPAK_DEST/share/licenses/$FLATPAK_ID/global-shortcut-portal/`,
  next to the license the manifest already installs there.

What "offer" means per channel: bundling the source **inside** the
artifact satisfies GPLv3 §6 / AGPLv3 §6 in their strongest form
(source accompanies the work, machine-readable, no further charge).
Belt-and-braces beyond that, at release time (operator checklist, no
workflow wiring in this change): publish the sdist **URL next to the
artifact** — for the apt channel, drop it beside the deb in the Pages
repo; for Flathub, the manifest's own source entry already makes the
public build carry it.

Trade-offs:

- full functionality is preserved — the portal hotkey (native
  hold-to-talk edges on GNOME 48+/Plasma 5.27+/Hyprland) is exactly the
  Wayland polish a paid SKU sells;
- the obligations are one-time and mechanical: content pins are
  enforced by tests (a wheel/sdist bump that forgets `paidartifact.py`
  fails the suite);
- the AGPL component remains in the paid artifact's compliance
  surface — e.g. AGPL §13's network-terms analysis must stay current
  (today it is trivially satisfied: no network listener exists,
  [ADR-0001](ADR-0001-no-tcp.md)).

## Option B — paid builds drop the AGPL wheel

The paid artifact contains no AGPL code at all.

Mechanics (implemented):

- **deb** — the guard (`paidartifact.py deb-guard`) asserts the AGPL
  package never enters the constraints lock; it enters via nothing
  else, so the deb's dependency set is exactly today's.
- **Flatpak** — the `flatpak` transform removes the wheel from the
  deps `pip3 install --no-index` line, removes its license-install
  line and its `type: file` source entry (a comment marks each removal
  site for reviewers).

What is lost, where: the GlobalShortcuts portal hotkey inside the
sandbox — on Wayland desktops with the portal (GNOME 48+,
Plasma 5.27+, Hyprland) users fall back to a DE custom shortcut bound
to the app's toggle command, and lose the portal's native
activated/deactivated hold-to-talk edges
([docs/guides/wayland.md](../guides/wayland.md)); evdev push-to-talk
remains the documented privileged opt-in. EIS text insertion is
unaffected (dbus-next, MIT, stays). X11 installs are unaffected.

Trade-offs:

- zero AGPL coupling: nothing about the paid artifact's source duties
  changes with the wheel's upstream licensing;
- the cost recurs on every Wayland desktop the product targets: the
  paid build is strictly less polished than the free Flatpak on the
  hotkey axis;
- free and paid artifacts diverge (free keeps the wheel), so the two
  builds exercise different hotkey paths.

## Recommendation (analysis, not the decision)

Option A. Its obligations are one-time mechanical and already
implemented and tested; Option B pays a recurring UX cost in the exact
segment (Wayland polish) the paid SKU monetizes. B remains the correct
fallback if the operator wants zero AGPL surface or a channel's review
process makes bundled sources awkward.

## Decision

**OPERATOR.** Pick by setting the policy at paid-build time —
`PAID_AGPL_POLICY=source` (A) or `PAID_AGPL_POLICY=drop-portal` (B) —
then record the choice here and move this ADR to Accepted (or write the
superseding note if the choice reverses this analysis). The switch
refuses `PAID_ARTIFACT=1` without an explicit policy precisely so no
default is assumed silently while the decision is open.

## Consequences

- `packaging/paidartifact.py` is the single switch implementation:
  `resolve` (env validation), `filter_wheels` (dependency-set
  filtering — drops exactly the AGPL wheel; dbus-next stays),
  `deb_lock_guard`, the flatpak manifest transform, and the vendored
  artifact pins/verification.
- [tests/test_paid_artifact.py](../../tests/test_paid_artifact.py)
  pins both branches offline with fakes (plus the committed manifests
  through the structural parser of
  [tests/test_flatpak_manifest.py](../../tests/test_flatpak_manifest.py));
  no deb build or flatpak-builder runs in the unit tier.
- **Rehearsal** (paid-build dry run, no store, no cost):
  - deb: `PAID_ARTIFACT=1 PAID_AGPL_POLICY=source packaging/build-deb.sh`
    (then again with `drop-portal`) — an invalid combination fails
    before any staging; inspect
    `/usr/share/licenses/sayit-ermano/global-shortcut-portal/` in the
    built package for A, and the guard's output for B;
  - flatpak (needs flatpak-builder + the GNOME 51 SDK, so it is a
    documented operator rehearsal rather than a unit-tier test):
    `PAID_ARTIFACT=1 PAID_AGPL_POLICY=drop-portal python3
    packaging/paidartifact.py flatpak
    packaging/io.github.acailic.SayItErmano.yml -o /tmp/paid.yml &&
    flatpak-builder --user --force-clean --repo=/tmp/paid-repo
    .flatpak-build /tmp/paid.yml` — repeat with `PAID_AGPL_POLICY=source`
    and/or the `.flathub.yml` variant.
- Free builds are unchanged: the switch defaults off, and the
  committed manifests and release flow are untouched (no
  `.github/workflows/` edits in this change).
- Any wheel/sdist version bump must re-pin in `paidartifact.py` and
  both manifest variants in the same commit — the tests fail otherwise.
