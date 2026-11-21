Non-Flatpak installs need an update channel that does not depend on
reading the README (GitHub #22, milestone v1.x).

STATUS: SHIPPED

<!-- this branch: apt repo (reprepro/Pages) + one-shot --update + manual
     in-app check; shipped with the deb-configures-the-repo design -->

"Updates" is half of what the one-time price buys: Flatpak users get
Flathub; deb/AUR/installer users got nothing but release-URL copy-paste.

Scope delivered:
1) Self-hosted signed apt repo: reprepro tree committed at site/apt,
   deployed by the existing Pages workflow (site/** trigger), signed with
   a dedicated key (public half committed at packaging/apt/sayit-ermano.asc,
   private half in the APT_REPO_GPG_KEY secret - one-time provisioning in
   packaging/apt/README.md). release-publish runs packaging/apt/update-repo.sh
   after the GitHub release: drops older versions (single-version policy,
   bounded Pages size), includes + signs the new deb, VERIFIES like a
   client (sandboxed apt update + policy + hash download), commits
   site/apt to linux. The .deb points at it: build-deb.sh ships
   /etc/apt/keyrings/sayit-ermano.gpg + /etc/apt/sources.list.d/
   sayit-ermano.list as conffiles, so apt upgrade moves n -> n+1 with
   config (~/.config/sayit-ermano) and history untouched (the package
   never writes there - VM evidence lands in the #14 matrix,
   docs/quality/).
2) install-one-shot.sh --update: in-place update, route detected (user
   venv falls through to the reinstall flow; pipx upgrade - works on
   Fedora, no dpkg involved; system deb via the apt repo or latest .deb;
   nothing installed -> refuse with install hint). Covered by
   tests/test_installer_update.py (sandboxed PATH/HOME stub harness; the
   in-place test pins config/history survival byte-for-byte).
3) In-app update notice (no-telemetry ADR posture): control route
   update-check + fluidvoice/update.manual_check (never notifies, never
   writes state, works with updates.check=false); Settings -> About gets
   a Check-for-updates button (off-thread socket call) and a How-to-update
   button deep-linking docs/guides/install.md#updates; a failed/offline
   check surfaces "latest unknown (offline ...)" instead of "up to date"
   (tested at update.py, route and UI levels). update.py detects the apt
   route (sources.list.d marker) so `sayit-ermano update` prints
   `sudo apt update && sudo apt upgrade -y sayit-ermano`.

Out of scope: Flathub autoupdates (native), AUR (makepkg -si refreshes),
any CI push triggers (house rule - the apt publish is a step of the
MANUAL release-publish workflow only), serving old versions/downgrades.
