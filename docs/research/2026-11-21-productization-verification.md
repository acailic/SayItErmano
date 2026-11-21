# Productization verification: market, payment rails, Flatpak input

Date checked: 2026-11-21. Status: launch-decision evidence for the
productization board (GitHub Project "SayItErmano — Productization");
feeds issues #10–#13, #15–#18, #25, which were refined with these
findings the same day.

Three questions were verified against primary sources, all fetched on
the check date unless noted: (A) what the dictation market actually
charges and whether the prior claims hold; (B) which payment rail a
Serbia-based solo seller of a GPL one-time product can use; (C) how a
sandboxed Flatpak build can keep a global push-to-talk hotkey and text
insertion.

## A. Market verification

Prices are the official pages' displayed amounts on the check date.

| Product | Verified offer | Platform | Source | Confidence |
|---|---|---|---|---|
| **Voicy** | Free trial 30 min; Pro $8.49/mo, $82/yr; Lifetime $260; Teams $6.79/user/mo. Cloud-default; offline mode (25 langs) after model download; AI rewrite online | **Linux native** (.deb/.rpm/AppImage), Mac, Windows, browser, mobile | [usevoicy.com/pricing](https://usevoicy.com/pricing/), [/dictation-for-linux](https://usevoicy.com/dictation-for-linux/) | High |
| **MacWhisper** | Free forever; Pro EUR 64 one-time (Gumroad lists EUR 65; 5–50 packs; 7-day refund). App Store edition ("Whisper Transcription") IAPs up to Pro Lifetime $99.99; 176 US ratings, 3.9★ | macOS | [macwhisper.com](https://macwhisper.com) (macwhisper.eu 301s here; late.eu parked), [App Store](https://apps.apple.com/us/app/id1668083311) | High |
| **Vibe Typer** | Free 2,000 words + 20 AI ops/mo; Pro $10/mo or $8/mo annual ($96/yr) | Linux, Mac, Windows, mobile | [vibetyper.com/pricing](https://vibetyper.com/pricing) | High |
| **OpenWhispr** | Local dictation free; Pro $80/user/yr, Business $160. 11,945★, MIT | Linux, Mac, Windows (+ paid iOS) | [openwhispr.com/pricing](https://openwhispr.com/pricing), GitHub API | High |
| **Wispr Flow** | Free; Pro $15/user/mo, $12 annual; Growth $23/$18. **No Linux client** — docs: "There is no native Linux application"; downloads page is a vote-only waitlist; staff on r/WisprFlow (2026-03-09): not on roadmap | Mac, Windows, iOS, Android | [docs](https://docs.wisprflow.ai/articles/6478598909), [downloads](https://wisprflow.ai/downloads) | High |
| Wispr funding | $280M Series B at $2B valuation, 2026-08-17; revenue **undisclosed**; Fortune: +150%+ in each of the last 4 quarters. An unofficial community port exists (212★) | — | [series-b post](https://wisprflow.ai/post/series-b), Reuters/TechCrunch/Fortune 2026-08-17 | High (funding) / no ARR disclosure exists |
| **Handy** | Free, MIT, offline STT. **39,439★**, 3,724 forks | Cross-platform desktop | [GitHub API](https://api.github.com/repos/cjpais/handy) | High |
| **Vocalinux** | Free, AGPL, 1,036★; Flathub submission PR closed unmerged (see §D) | Linux | [GitHub API](https://api.github.com/repos/VocaHQ/vocalinux), [flathub PR #9368](https://github.com/flathub/flathub/pull/9368) | High |
| **Voxtype** | Now free/open source (MIT), no paid tier; v0.7.0, 1,809★ | Linux, macOS | [voxtype.io](https://voxtype.io), [GitHub](https://github.com/peteonrails/voxtype) | High |
| Voxtty | Free MIT core; Pro "planned" $5/mo or $50/yr (site internally contradictory: "not on sale yet") | Linux | [voxtty.com](https://voxtty.com) | Med |

Prior claims from the productization shortlist that did **not** check
out:

1. "Wispr Flow ~$10M ARR" — stale third-party estimate from ~Oct 2025;
   no official revenue figure exists. Use the Series B facts instead.
2. "Flathub dictation search returns only CLI tools" — literally false
   (all GUI apps), materially half-true: no polished push-to-talk +
   AI-polish product exists there and the only proprietary listing is
   meeting-focused. The shelf is winnable.
3. MacWhisper price is EUR 64 (not 49); the "≈2900 reviews" figure was
   not found — the US App Store edition shows 176 ratings. Do not reuse
   either number without a fresh source.

### Flathub search reality (2026-11-21)

`POST https://flathub.org/api/v2/search` with `{"query": ...}` returns
`hits[]` whose only install metric is `installs_last_month` (GET with
query params returns 405; there is no cumulative total):

- "dictation" (19 hits): #1 Speed of Sound — 1,515/mo (MIT, voice
  typing); Loomfy — 432/mo (proprietary, meetings); Dabri — 974/mo;
  Buzz — 4,028/mo (file transcription); Whis — 628/mo.
- "speech to text" (21 hits): #1 Speech Note — 8,778/mo (the category
  ceiling).
- "whisper": #1 is `it.mijorus.whisper`, a mic-monitoring utility — an
  unrelated namesake that owns the keyword. Do not build discoverability
  on "whisper" alone.
- Not on Flathub at all (verified by exact-name queries): Handy,
  Vocalinux, Voxtype, OpenWhispr, Voicy, Vibe Typer.

## B. Payment rail for a Serbia-based solo seller (GPL, one-time)

**Lemon Squeezy is not an option**: it stopped accepting new stores and
announced a wind-down — processing ends 2027-03-31
([homepage](https://www.lemonsqueezy.com/), [goodbye post](https://www.lemonsqueezy.com/blog/saying-goodbye-to-lemon-squeezy),
[migration docs](https://docs.lemonsqueezy.com/migration)). Its
successor, Stripe Managed Payments, requires a Stripe-supported business
location ([eligibility](https://docs.stripe.com/payments/managed-payments/eligibility));
Serbia is absent from [stripe.com/global](https://stripe.com/global).

| Provider | Fees | MoR + VAT? | Serbia seller | License keys | Payout | Confidence |
|---|---|---|---|---|---|---|
| **Paddle** | 5% + $0.50/checkout | Yes (seller of record, tax registration) | Yes — "anywhere in the world" except sanctioned; Serbia not excluded | Billing keys **deprecated** — self-issue via `transaction.completed` webhook | Monthly by the 15th, min $100, wire or Payoneer | High |
| **Polar.sh** | 5% + $0.50; +1.5% intl cards; $15/dispute | Yes (Global MoR) | Yes — 🇷🇸 explicitly in Stripe Connect Express list (verify business type at onboarding) | **Native**: prefix, expiry, activation/device limits, validation API, customer portal | On-demand via Stripe Connect Express | High |
| Gumroad | 10% + $0.50 (+ unpublished processing) | Yes (since 2025-01-01) | Yes — **RSD local bank** (min 3,000) or PayPal | Generate-on-sale + verify API | Bank/PayPal | High |
| FastSpring | Not published (sales-led) | Yes | Unverified | Built-in generators | 2×/mo, $100 min, 45-day initial hold | Low (fees) |
| itch.io | Rev share you set (default 10%) + 2.9% + $0.30 | Yes in "Collected by itch.io" mode | Yes (PayPal/Payoneer; no explicit country list) | **No purchase webhook** — breaks automated key issuance | PayPal/Payoneer, min $5 | High |

Sources: [Paddle pricing](https://www.paddle.com/pricing), [countries](https://www.paddle.com/help/start/intro-to-paddle/which-countries-are-supported-by-paddle), [payouts](https://www.paddle.com/help/manage/get-paid/when-and-how-do-i-get-paid), [feature comparison](https://developer.paddle.com/migrate/learn/feature-comparison/);
[Polar fees](https://polar.sh/docs/merchant-of-record/fees), [countries](https://polar.sh/docs/merchant-of-record/supported-countries), [license keys](https://polar.sh/docs/features/benefits/license-keys), [downloads](https://polar.sh/docs/features/benefits/file-downloads);
[Gumroad pricing](https://gumroad.com/pricing), [getting paid](https://gumroad.com/help/article/13-getting-paid), [license keys](https://gumroad.com/help/article/76-license-keys);
[itch.io payments](https://itch.io/docs/creators/payments).

**Recommendation: Paddle** as the primary rail (cheapest published
all-in rate, full MoR kills all VAT plumbing for a Serbian sole trader,
Serbia sellers explicitly served); **Polar.sh** runner-up (Serbia OK,
native keys + hosted downloads, OSS-native — VoiceInk precedent:
[license docs](https://tryvoiceink.com/docs/license-management) route
buyers to the Polar portal). Gumroad is the validated fallback
(MacWhisper sells via Gumroad,
[licensing](https://docs.macwhisper.com/article/39-licensing)). No GPL
friction found on any rail; GPLv3 permits paid copies
([GNU FAQ](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowMoney)).

Offline-key design inputs: signed-key (Ed25519) over
payload {product, email, purchase date, max version} with an embedded
public key is the standard; HMAC needs a secret in the binary and is
weaker. References: [keygen offline licensing](https://keygen.sh/docs/choosing-a-licensing-model/offline-licenses/),
[keygen cryptography](https://keygen.sh/docs/api/cryptography/),
[example Ed25519 verification](https://github.com/keygen-sh/example-cpp-ed25519-verification).
superwhisper's `superwhisper://license/KEY` deep link is a UX pattern
worth copying.

## C. Flatpak sandbox: global hotkey + text insertion

### Hotkey — GlobalShortcuts portal is the primary path

- Spec is **v2**; `Activated`/`Deactivated` events give press+release →
  native hold-PTT. `BindShortcuts` may be called once per session.
  [Spec](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html)
- Backend matrix **today**: GNOME **48+** ([release notes](https://release.gnome.org/48/developers/); 46.2 lacks it), KDE **Plasma 5.27+**, Hyprland (xdg-desktop-portal-hyprland); **sway/wlroots: no** ([xdg-desktop-portal-wlr#240](https://github.com/emersion/xdg-desktop-portal/issues/240), open since 2022); COSMIC: no.
- Real Flatpak apps shipping it: OBS Wayland-hotkeys plugin, Kando,
  Super Productivity (Electron ≥41). Telegram Desktop: unverified.
- Known bugs to code around: GNOME 49 empty-app_id (a Flatpak app-id is
  always set — dodged); KDE description changes fixed only in 6.7.4.
- InputCapture is **not** a PTT trigger — its only defined trigger is
  pointer barriers ([spec](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html)); shortcut-trigger is an unmerged RFC.
- Future: `xx-hotkey-v1` Wayland protocol (merged 2026-09; Hyprland
  shipped 2026-10; wlroots pending) — native `triggered`/`released`,
  ideal PTT semantics, track but do not gate.

### Insertion — RemoteDesktop portal + libei/EIS

- The sanctioned path since portal 1.17; GNOME ≥46 via `ConnectToEIS`
  ([xdg-desktop-portal-gnome](https://github.com/GNOME/xdg-desktop-portal-gnome/blob/main/src/remotedesktop.c));
  KWin EIS backend exists, keysym layout fix landed 2025-09
  ([who-t 2026-07-22](https://who-t.blogspot.com/2026/07/libei-integrations-in-xdg-remotedesktop.html)).
- Consent: session persistence on portal ≥1.21; per-session dialog on
  older. Document both flows.
- EIS cannot upload keymaps → out-of-layout Unicode needs clipboard +
  EIS-sent Ctrl+V fallback; `ei_text` UTF-8 (libei 1.6, 2026-05) is the
  future fix.
- `zwp_virtual_keyboard_manager_v1` (wtype) is **wlroots-family only**:
  mutter will not implement it
  ([mutter#1974](https://gitlab.gnome.org/GNOME/mutter/-/issues/1974)),
  KWin does not either
  ([KDE bug 512996](https://bugs.kde.org/show_bug.cgi?id=512996)).
  Gate on a runtime protocol probe, not DE name. (The `wrtype` crate's
  docs claiming GNOME/KDE support are wrong — discarded.)
- X11: keep XGrabKey + xdotool for true X11 sessions only. Xwayland
  ≥23.2 routes XTEST through the RemoteDesktop portal with a prompt;
  GNOME 49+ drops X11 sessions.
- Escape hatch (opt-in, documented): ydotool + `--device=all` — host
  udev/permission caveats; Flathub reviewers push back (§D).

### Precedents

**Handy** (39.4k★ dictation app) is migrating to Flatpak with exactly
this stack: GlobalShortcuts portal with press/release PTT
([#1287](https://github.com/cjpais/Handy/pull/1287)) + RemoteDesktop
text pipeline with Unicode fallback
([#689](https://github.com/cjpais/Handy/pull/689)), `--socket=wayland`
only, with a `handy --toggle` CLI/DE-shortcut fallback
([META#1555](https://github.com/cjpais/Handy/issues/1555)).
**Vocalinux**'s Flathub submission was closed with the reviewer
directing them to the GlobalShortcuts portal
([flathub PR #9368](https://github.com/flathub/flathub/pull/9368)).
**espanso** is not on Flathub; its sandbox-input issue is exactly the
one the portal path solves
([#2275](https://github.com/espanso/espanso/issues/2275)).
**KDE Connect** injects input via RemoteDesktop+EIS on Flathub today.

### GPU in the sandbox

NVIDIA GPU access comes from the `org.freedesktop.Platform.GL.nvidia-*`
extension; **Vulkan works** (Blender's manifest pattern with
`--device=dri`). **CUDA in Flatpak has no verified precedent** and a
breakage history ([GL.nvidia#184](https://github.com/flathub/org.freedesktop.Platform.GL.nvidia/issues/184),
[Blender #110298](https://projects.blender.org/blender/blender/issues/110298)).
Decision input: Flatpak GPU path = whisper.cpp Vulkan; CUDA stays a
host-build advantage. (whisper.cpp Vulkan itself: not separately
verified this pass.)

## D. Flathub submission requirements (2026)

- Route: GitHub PR against flathub/flathub `new-pr` branch;
  submission-checker bot runs hourly; every finish-arg justified
  ([docs](https://docs.flathub.org/docs/for-app-authors/submission)).
- **Stable-first**: new apps must target stable; a beta branch exists
  only after acceptance and is not listed on the website
  ([requirements](https://docs.flathub.org/docs/for-app-authors/requirements)).
- Policy: tray-only apps rejected (a full Settings window satisfies
  this); host-dependent apps rejected (portal paths from §C are what
  make SayItErmano pass); demo video required by the submission
  template; MetaInfo license must match source (GPL-3.0 ✔).
- App ID: reverse-DNS from a controlled domain; the new backend rejects
  new `com.github.*`/`com.gitlab.*` ids. `io.github.acailic.SayItErmano`
  (github.io) qualifies; renames force resubmission.
- **Generative-AI policy**: manifests and the submission PR (text,
  commit messages, descriptions, review comments, replies) must contain
  no AI-generated or AI-assisted content, and AI tools/agents must not
  open or automate the PR. The submission PR is human-only work.
- Selling binaries elsewhere raises no policy issue — the monetisation
  policy governs in-app payments only.
- No documented review SLA; the observed Vocalinux timeline: bot checks
  ~hourly, first human review < 1 day, closed day 6.

## Consequences applied to the board (same day)

- #10 reframed from research to "ADR + spike" on the verified
  architecture; risk High → Medium (Handy ships the same stack).
- #11 pinned the finish-args baseline (no `--device=all` default),
  daemon-without-systemd requirement, Vulkan GPU scope, app id choice.
- #12 inverted to stable-first, added the demo-video and AI-content
  constraints, keyword set, and install benchmarks.
- #13 gained version minimums, non-US-layout and lifecycle rows.
- #15 received the verified comp table and net-per-sale fee models;
  the "~$10M Wispr ARR" line is banned.
- #16 retargeted the webhook to Paddle `transaction.completed`
  (Paddle's built-in keys are deprecated).
- #17 retitled to Paddle with the Lemon Squeezy wind-down as the reason.
- #18 gained the Flathub metric mechanics (`installs_last_month`,
  benchmarks 1,515 and 8,778/mo).
- #25 gained the exact watchlist (URLs, cadence, alert triggers).

## Method and limits

Three parallel research agents, ~55 primary pages fetched 2026-11-21
(market/pricing pages, vendor docs, Flathub API, GitHub API, Wayland
portal specs and trackers). Raw page captures were retained by the
researching session. No checkout, account creation, seller onboarding,
benchmark, or legal-rights audit was performed. Fees are the vendors'
published list rates; actual statements at onboarding govern. Star
counts are point-in-time GitHub API reads. Items marked unverified
(FastSpring pricing, whisper.cpp Vulkan in-sandbox, Telegram portal
usage) failed verification this pass and carry no inference beyond
"unknown".
