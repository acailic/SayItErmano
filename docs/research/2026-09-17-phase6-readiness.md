# Phase 6 readiness — 2026-09-17

Source checked: `a3cab30c094af930dc0189876da7a7bdcbb43d86`.
Environment: Pop!_OS 24.04 LTS, Linux 7.1.5-76070105-generic,
Python 3.12.3. Checks ran from an isolated worktree using the main
tree's shared interpreter through `SAYIT_PY`.

This is a readiness check, not live Wayland, speech-quality, installation,
or soak acceptance evidence. The two OPEN request briefs remain OPEN.

## Available infrastructure

| Observation | Evidence | Consequence |
|---|---|---|
| Current desktop is X11 | `XDG_SESSION_TYPE=x11`, `DISPLAY=:1`, no `WAYLAND_DISPLAY` | Cannot execute the Wayland rows in this login |
| GNOME Wayland session entries are installed | `/usr/share/wayland-sessions/gnome-wayland.desktop` and `gnome.desktop`; `/usr/bin/gnome-session` exists | Earlier documentation saying no compositor is installed is stale; installed entries alone do not prove a working Wayland login |
| GDM explicitly disables Wayland | `/etc/gdm3/custom.conf:7`: `WaylandEnable=false` | A maintainer-arranged login/configuration change is needed before testing |
| sway, wtype, ydotool and ydotoold are absent from PATH | `command -v` found none; wl-copy and wl-paste exist | Prepare the compositor-specific insertion tools before the normal insertion rows; keep the absent-tools cases in the matrix |
| Virtualization tools exist, but no local libvirt guests are defined | `virsh -c qemu:///session list --all` and `virsh -c qemu:///system list --all` both empty; qemu-system-x86_64 exists | Q9 still needs disposable target guests; this does not rule out unregistered disk images |
| `/dev/kvm` is absent | Filesystem check | Hardware acceleration is not currently exposed; resolve this or allow for software emulation before estimating VM test time |
| Production daemon is active | `systemctl --user is-active sayit-ermano` returned `active` | Coordinate live tests using the repository stop/run/restore procedure |
| Interim synthetic speech baseline is already merged | `2cc130c` is an ancestor of the checked SHA | Do not repeat the old merge action; it does not satisfy real-speech evidence |

No display-manager configuration, daemon configuration, service state,
or recordings were changed by this check.

## Next executable sequence

1. Arrange a GNOME Wayland login after addressing the explicit GDM
   setting. Do not restart the display manager during an active desktop
   session. Confirm the resulting session type and compositor before
   recording any result.
2. Execute the existing [Wayland/context matrix](../dev/wayland-smoke-matrix.md)
   and [desktop matrix](../dev/desktop-matrix.md), including context off/on,
   missing-tool behavior, and the X11 control rows. Record source SHA,
   app/tool versions and per-cell observations. Repeat the required sway
   rows in a real sway session. Restore the production daemon and any
   temporary configuration changes afterwards.
3. For Q9, provision Ubuntu 24.04 and Arch disposable guests and take
   snapshots. Follow the [quality plan's Q9 sequence](../plans/archive/2026-09-11-project-quality-and-testing-plan.md):
   install, first run, upgrade, restart, rollback, uninstall; check retained
   config/history and bind results to package hashes. Exercise realistic
   take/reload/cancel/idle cycles during the required soak. An idle-only
   RSS sample does not meet that acceptance criterion.
4. For real speech, obtain the outstanding maintainer decision on the
   [consent template](../eval/consent-text.md) and private corpus storage,
   then collect each participant's consent before recording under the
   [corpus specification](../eval/corpus-spec.md). No recruitment or
   recording occurred during this check.

Recovery UX and commercial implementation retain their existing evidence
dependencies; this readiness report does not clear them.

## Refreshed automated baseline

All commands used
`SAYIT_PY=/home/nistrator/Documents/github/FluidVoiceLinux/.venv/bin/python`.
These results cover the checked source SHA, before the documentation-only
readiness update.

| Command | Result |
|---|---|
| `just gate` | PASS: lint, docs links, config reference freshness, 33 request briefs; 3508 unit tests passed, 4 skipped, 172 deselected, 100.33 s; warnings treated as errors |
| `just test-ui` | PASS: 172 tests, 7.16 s, isolated Xvfb |
| `xvfb-run -a just test-process` | PASS: 13 tests, 27 deselected, 12.17 s |
| `just typecheck` | PASS: 9 source files |

The four unit skips are backend-contract capability cases: one adapter
has confidence signals and three have segments. Xvfb results establish
automated display/process behavior only, not real Wayland compatibility.
