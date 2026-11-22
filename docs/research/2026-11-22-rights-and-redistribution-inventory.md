# Rights and redistribution inventory (monetization research question 2)

Date: 2026-11-22. Status: research note, not a legal opinion and not an
offer decision. Repository inspected: `e22275b` on `linux` (shallow clone,
one commit — see [Method and limits](#method-and-limits)).

Question ([plan](../plans/2026-09-11-product-excellence-and-monetization-plan.md),
"Monetization research" #2, lines 161–166): what can be sold under the
project's rights before any paid offer. Companion evidence:
[commercial-model research](2026-09-11-commercial-model-evidence.md),
[ADR-0005](../adr/ADR-0005-commercial-sku.md), [PRICING](../PRICING.md).

## Bottom line

- **Selling is permitted.** GPLv3 §4: "You may charge any price or no
  price for each copy that you convey, and you may offer support or
  warranty protection for a fee" (`LICENSE:205-206`); the FSF FAQ
  confirms selling copies and charging for downloads
  ([#DoesTheGPLAllowMoney](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowMoney),
  [#DoesTheGPLAllowDownloadFee](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowDownloadFee)).
- **A proprietary edition, restrictive EULA or dual licence is not
  available.** The prompts, rule tables and both sounds are inherited
  from upstream FluidVoice, whose licence statement is GPLv3
  ([inventory](#1-inventory-by-origin)). Recipients' freedoms cannot be
  restricted (`LICENSE:463`, §10).
- **Every package licence verified is GPLv3-compatible.** No GPL-2.0-only,
  LGPL-2.1-only or non-free term was found in any channel. Two copyleft
  facts shape packaging rather than the offer: an **AGPL-3.0-only**
  library in the Flatpak/`[portal]` extra, and **GPL/LGPL native code
  inside the pinned PyAV wheel** that the deb redistributes
  ([sweep](#2-dependency-licensing-sweep)). The 32 libraries inside that
  wheel are identified, not all individually licensed (O1, O2, O15).
- **Model weights are not redistributed today** (downloaded by the user
  at first use). Their upstream terms are MIT / Apache-2.0 / CC-BY-4.0,
  so bundling is possible with notices and attribution (O3, O4 open).
- **Gaps to close before a paid build** ([section 4](#4-gaps-to-close-before-a-paid-build)):
  the deb carries bundled native code and four wheels without their
  licence texts or a source offer; per-file licence notices are absent;
  third-party brand assets have no recorded licence; the "or later" grant
  rests on packaging metadata only.

## 1. Inventory by origin

### 1.1 Repository facts

| Fact | Value | Evidence |
|---|---|---|
| Repository relation | `acailic/SayItErmano` is a GitHub **fork** of `altic-dev/FluidVoice` (created 2026-09-01) | `gh api repos/acailic/SayItErmano` → `fork: true`, `parent.full_name: altic-dev/FluidVoice` |
| GitHub licence label | `GPL-3.0` on both repositories (the detector reads the licence file) | same call; `gh api repos/altic-dev/FluidVoice` |
| Declared licence | `GPL-3.0-or-later` | `pyproject.toml:11,17`; metainfo `<project_license>` (`packaging/flatpak/io.github.acailic.SayItErmano.metainfo.xml:37`); `packaging/aur/PKGBUILD` `license=('GPL-3.0-or-later')` |
| Licence file | Stock GPLv3 text, byte-identical to upstream's `LICENSE` at `b395a7a`; the "How to apply" template is unfilled | `git hash-object LICENSE` = `f288702d…` = upstream tree blob `f288702d…` (`gh api repos/altic-dev/FluidVoice/git/trees/b395a7af0242b6869867abdd61e245a5a80ec218?recursive=1`) |
| Upstream licence statement | "From 2026-02-23 onward, this project is licensed under the GNU General Public License, Version 3.0 (GPLv3)… Versions published before this date were licensed under Apache License 2.0." | upstream README, `gh api repos/altic-dev/FluidVoice/readme`, "License" section |
| Authorship | 408 commits by `acailic`, 11 by the automation bot (GitHub's count; the clone itself has one commit) | `gh api repos/acailic/SayItErmano/contributors` |
| Inherited branches | 175 branches on the remote, 170 non-`agent/*` (AGENTS.md says "~300") | `gh api repos/acailic/SayItErmano/branches --paginate` |

### 1.2 The tree, by origin

| Part | Where | Origin | Licence | Evidence | Status |
|---|---|---|---|---|---|
| Python package (109 `.py` files) | `fluidvoice/` | Linux implementation of upstream **behaviour**, written for this port; 21 lines in 12+ files name FluidVoice (`fluidvoice/ai/prompts.py`, `processing/punctuation.py`, …) | GPL-3.0-or-later (declared) | `README.md:33-40`; `fluidvoice/__init__.py:1-11` | OK. Per-file notices exist in 4 of 109 files → [G5](#4-gaps-to-close-before-a-paid-build) |
| Dictation/edit prompts | `fluidvoice/ai/prompts.py`, `docs/BEHAVIOR-SPEC.md` §1 | **Copied verbatim from upstream** | GPLv3 (upstream) | `docs/BEHAVIOR-SPEC.md:9,104` ("verbatim, GPLv3", "verified byte-identical"); `fluidvoice/ai/__init__.py:2-4`; `README.md:91` | Inherited — cannot be relicensed |
| Rule tables (spoken punctuation, slash/mention gates, destructive-command list) | `fluidvoice/processing/`, `fluidvoice/command*.py` | **Ported verbatim** from named upstream sources | GPLv3 (upstream) | `docs/UPSTREAM-TRACKING.md:67,71,72` (`TerminalService.swift:20-61`, `CommandModeService.swift:562-598`); `docs/BEHAVIOR-SPEC.md:172,184` | Inherited — cannot be relicensed |
| Start/stop sounds | `fluidvoice/assets/sfx/FV_start_0.m4a`, `FV_end_0.m4a` | **Byte-identical to upstream** `Sources/Fluid/Resources/` at `b395a7a` | GPLv3 (upstream) | git blob ids `227b5650…` / `6a4c74e8…` identical on both sides (`git hash-object` vs upstream tree API); `README.md:92,300-302` | Inherited. Who recorded the sounds: **OPEN** (O8) |
| App icon | `fluidvoice/assets/icon.png`, `packaging/icons/hicolor/` | Original artwork, generated by `scripts/gen-app-icon.py` | Project's own | `design/README.md` ("App icon — single source") | OK |
| Symbolic UI icons (6 SVG) | `fluidvoice/assets/icons/symbolic/actions/` | Not documented; no copyright/licence metadata inside the files | unknown | `ls` + grep of the SVGs for licence strings (none) | **OPEN** (O7) |
| Provider logos (16 fetched PNG + the 2 own GLM marks) | `fluidvoice/assets/providers/` | "fetched brand assets… not produced by this repo" | none recorded | `design/README.md` ("Provider logos") | **OPEN — trademarks** (O6) |
| GLM mark | `glm.png`, `glm-light.png` | Generated in-repo with the DejaVu Sans Bold face | Project's own render; font-rendering terms not re-verified | `tools/gen-glm-logo.py` | Low risk; **OPEN** (O9) |
| Name "SayItErmano", app id `io.github.acailic.SayItErmano`, commands | repo, `pyproject.toml:6,57-58`, metainfo | Project's own | n/a | `README.md:42-46` | Trademark registration: **OPEN** (O10) |
| Name "FluidVoice", module name `fluidvoice`, legacy alias, `Provides: fluidvoice-linux` | `pyproject.toml:59,62`; `packaging/build-deb.sh:195-197` | Upstream's name, kept "on purpose" with a non-affiliation notice | n/a (trademark) | `README.md:306-308`; `fluidvoice/__init__.py:7-11` | Upstream trademark policy: **OPEN** (O10) |
| Website fonts | `site/assets/fonts/Geist-*.woff2` | Geist (Vercel) | SIL OFL 1.1 (`Geist-OFL.txt` beside the files) | `ls site/assets/fonts` | Site only, not in the app |
| UI translations | `po/sr.po`, `sr-Latn.po`, `bs.po`, compiled `.mo` | Translation catalogues; headers carry no translator/copyright line | unclear | `sed -n 1,9p po/sr.po` | Low risk; **OPEN** (O9) |
| Sale, refund and privacy pages | `site/terms.html`, `site/refund.html`, `site/privacy-policy.html` (dated 2026-11-21; "Seller: acailic (individual), Serbia") | Project's own legal text | n/a | `site/terms.html` ("What is sold", "The software itself"), `site/refund.html` ("After a refund") | Terms scope the buyer's freedoms to "the free build" → [G7](#4-gaps-to-close-before-a-paid-build) |
| Vendored wheels | `packaging/wheels/` (4) | Third-party | see [2.3](#23-flatpak-channel) | wheel `METADATA` | OK except the AGPL item |
| Agent factory | `tools/factory/` | Vendored SSSF/ADW tooling ("Agent tooling, not SayItErmano product code"); no licence file, no headers found | unknown | `tools/factory/README.md:1-3`; `find tools -iname 'LICENSE*'` (none) | Not in the wheel (`pyproject.toml:61-62`), but in the published source tree → **OPEN** (O11) |
| Licence-key code | `fluidvoice/license.py`, `scripts/license_*` | Project's own; Ed25519 written "from the RFC reference" (`license.py:19-22`) | Project's own; the RFC code's terms were not verified | `docs/dev/license-ops.md` ("Everything is stdlib") | Low risk; **OPEN** (O9) |
| Docs, screenshots, site | `docs/`, `site/`, `design/` | Generated in-repo (`scripts/gen-doc-media.py`, `gen-language-media.py`) | Project's own | `ls scripts` | OK |

**Consequence.** Whether the port as a whole is a "translation" (the FSF
treats translation as modification, covered by the original's copyright —
[FAQ #TranslateCode](https://www.gnu.org/licenses/gpl-faq.html#TranslateCode))
or an independent re-implementation matters only if someone tries to
relicence. Verbatim prompts, rule tables and sounds are inherited either
way; the project already treats everything as GPLv3. Keep that.

**"Or later" caveat.** Upstream states plain "GPLv3" with an unfilled
template and no per-file notice, i.e. no "or any later version" grant
was found for the inherited files. This port's `-or-later` lives only in
packaging metadata. It has no practical effect today, but the combined
work should be treated as GPL-3.0 for the inherited parts → **OPEN**
(O5).

## 2. Dependency licensing sweep

Method: licence expressions read from the exact artifacts the channels
install — PyPI JSON for the pinned versions, wheel `METADATA` for the
vendored wheels, wheel file lists for licence texts, Hugging Face API
for model repositories. "Pin match" means the wheel's sha256 equals the
hash in `packaging/deb/constraints.txt` (25 of 26 downloaded and matched;
Pillow's pin is the `manylinux_2_28` variant).

### 2.1 Declared dependencies

| Scope | Packages | Source |
|---|---|---|
| Runtime (`dependencies`) | `python-xlib>=0.33`, `pillow>=10.1`, `faster-whisper>=1.0` | `pyproject.toml:21-25` |
| Optional extras | `[torch]` `openai-whisper`; `[parakeet]` `onnxruntime>=1.17`; `[wayland]` `evdev>=1.6`; `[portal]` `global-shortcut-portal>=0.1.0`, `dbus-next>=0.2.3` | `pyproject.toml:32-46` |
| Not installed by the one-shot installer | `scripts/install.sh:15` runs `pip install -e .` without extras; no `portal` reference in `scripts/install-one-shot.sh` | grep |

### 2.2 The deb / apt channel — hash-locked closure (26 wheels)

The deb installs this closure with `pip install --require-hashes`, then
the app `--no-deps`, into `/opt/sayit-ermano/venv`
(`packaging/build-deb.sh:93-98`). **The deb therefore redistributes
these wheels, including their compiled code.**

| Package == pin | Licence (PyPI metadata) | Licence text inside wheel | Native code bundled |
|---|---|---|---|
| anyio 4.14.2 | MIT | yes (`LICENSE`) | — |
| av 18.1.0 | BSD-3-Clause | PyAV's own `LICENSE.txt` only | **32 shared libs**, see 2.4 |
| certifi 2026.7.22 | MPL-2.0 | yes | — |
| click 8.4.2 | BSD-3-Clause | yes | — |
| ctranslate2 4.8.2 | MIT | **no** | `libctranslate2`, `libgomp` |
| faster-whisper 1.2.1 | MIT | yes | — |
| filelock 3.30.2 | MIT | yes | — |
| flatbuffers 25.12.19 | Apache-2.0 | **no** | — |
| fsspec 2026.6.0 | BSD-3-Clause | yes | — |
| h11 0.16.0 | MIT | yes | — |
| hf-xet 1.6.0 | Apache-2.0 | yes | `hf_xet.abi3.so` |
| httpcore 1.0.9 | BSD-3-Clause | yes | — |
| httpx 0.28.1 | BSD-3-Clause | yes | — |
| huggingface-hub 1.30.0 | Apache-2.0 | yes | — |
| idna 3.6 | BSD-3-Clause | yes | — |
| numpy 1.26.4 | BSD-3-Clause | yes (`LICENSE.txt`) | OpenBLAS, `libgfortran`, `libquadmath` |
| onnxruntime 1.29.0 | MIT | **no** | `onnxruntime_pybind11_state`, `libonnxruntime_providers_shared` |
| packaging 24.0 | Apache-2.0 / BSD-2 dual | yes (both texts) | — |
| pillow 10.2.0 | HPND | yes (checked on the sibling `manylinux_2_17` wheel) | libjpeg, freetype, libpng, libwebp, … |
| protobuf 7.36.1 | BSD-3-Clause | yes | `_message.abi3.so` |
| python-xlib 0.33 | **LGPLv2+** | yes (`LICENSE`) | — |
| PyYAML 6.0.1 | MIT | yes | `_yaml` |
| six 1.17.0 | MIT | yes | — |
| tokenizers 0.23.2 | Apache-2.0 | **no** (ships a CycloneDX SBOM only) | `tokenizers.abi3.so` |
| tqdm 4.68.4 | MPL-2.0 AND MIT | yes (`LICENCE`) | — |
| typing-extensions 4.16.0 | PSF-2.0 | yes | — |

System packages are `Depends`/`Recommends`, not conveyed by the project:
`python3`, `pipewire-audio-utils | pulseaudio-utils`, `xdotool`, `xclip`,
`libnotify-bin`, `python3-gi`, `gir1.2-gtk-4.0`, `gir1.2-adw-1`,
`pulseaudio-utils`, `llama.cpp` (`packaging/build-deb.sh:193-194`). The
AUR recipe likewise depends on distro packages (`packaging/aur/PKGBUILD`
`depends`). Their licences were not audited: not redistributed → **OPEN**
(O12, low).

### 2.3 Flatpak channel

Four wheels are vendored and installed verbatim
(`packaging/io.github.acailic.SayItErmano.yml:92`):

| Wheel | Licence (wheel `METADATA`) | Licence text inside |
|---|---|---|
| python_xlib-0.33 | `LGPLv2+` | yes |
| pillow-13.0.0 | `MIT-CMU` (`License-Expression`) | yes |
| dbus_next-0.2.3 | MIT | yes |
| global_shortcut_portal-0.1.0 | **`AGPL-3.0-only`** (`License-Expression`) | yes |

**Copyleft analysis of the AGPL item.**

- GPLv3 §13 in this repo: "you have permission to link or combine any
  covered work with a work licensed under version 3 of the GNU Affero
  General Public License into a single combined work, and to convey the
  resulting work… the special requirements of the GNU Affero General
  Public License, section 13, concerning interaction through a network
  will apply to the combination as such" (`LICENSE:552-561`).
- FSF: "You can always link GPLv3-covered modules with AGPLv3-covered
  modules, and vice versa" ([FAQ #AGPLGPL](https://www.gnu.org/licenses/gpl-faq.html#AGPLGPL)).
  Section 13 is inert for a program "not expressly designed to interact
  with a user through a network" ([FAQ #AGPLv3InteractingRemotely](https://www.gnu.org/licenses/gpl-faq.html#AGPLv3InteractingRemotely)).
- SayItErmano has no network listener: a user-owned Unix control socket
  and a stdio MCP bridge only ([ADR-0001](../adr/ADR-0001-no-tcp.md):19;
  [egress audit](2026-11-21-network-egress-audit.md), "0 TCP listeners").
- Verdict: **compatible**; the Flatpak build and any `pip install .[portal]`
  copy carry an AGPL obligation that is satisfied as long as the source
  of the shipped build is published — which the project does. The repo's
  own conclusion matches ([flatpak-permissions.md](../packaging/flatpak-permissions.md):78-85).
  Two documentation errors there → [G4](#4-gaps-to-close-before-a-paid-build).
  The deb closure does not contain this package (2.2).

### 2.4 Native code inside the pinned PyAV wheel

`av-18.1.0-cp311-abi3-manylinux_2_28_x86_64.whl` (sha256 = deb pin)
bundles FFmpeg and codec libraries in `av.libs/`. Verified from the
wheel bytes:

- Embedded configure line (in `libavcodec-5e6b81c2.so.62.28.102`):
  `--enable-version3 --enable-gnutls --enable-libdav1d --enable-libmp3lame
  --enable-libopencore-amrnb --enable-libopencore-amrwb --enable-libopus
  --enable-libsvtav1 --enable-libvpx --enable-libwebp --enable-libx264
  --enable-libx265 --enable-libvpl …` — 34 tokens, **no `--enable-gpl`
  and no `--enable-nonfree`**.
- Embedded banner: "libavcodec license: LGPL version 3 or later" (same
  string in the other `libav*` libraries).
- Bundled: `libx264-….so.165` (banner "x264 - core … H.264/MPEG-4 AVC
  codec - Copy… 2003-2025 - http://www.videolan.org/x264.html"),
  `libx265-….so.216`, `libgnutls`, `libnettle`, `libhogweed`, `libgmp`,
  `libmp3lame`, `libunistring`, `libasound`, `libopus`, `libvpx`,
  `libdav1d`, `libSvtAv1Enc`, `libvpl`, `libwebp*`, `libxcb*`,
  `libopencore-amr*`, `libXau`, `libdrm`, `libsharpyuv`.
- FFmpeg's own statement: "FFmpeg is licensed under the GNU Lesser
  General Public License (LGPL) version 2.1 or later. However, FFmpeg
  incorporates several optional parts and optimizations that are covered
  by the GNU General Public License (GPL) version 2 or later. If those
  parts get used the GPL applies to all of FFmpeg." Its compliance
  checklist adds "Make sure your program is not using any GPL libraries
  (notably libx264)" ([ffmpeg.org/legal](https://ffmpeg.org/legal.html)).

**Reading.** The deb contains GPL-family binaries. Every candidate
licence (GPL-2.0-or-later, LGPL-2.1-or-later, LGPL-3.0-or-later) is
compatible with GPLv3 — "if code is released under GPL 'version 2 or
later,' that is compatible with GPLv3"
([FAQ #v2v3Compatibility](https://www.gnu.org/licenses/gpl-faq.html#v2v3Compatibility));
among GNU licences the only blocked mix is code "*only* under an older
version" with newer code
([FAQ #AllCompatibility](https://www.gnu.org/licenses/gpl-faq.html#AllCompatibility)).
So **no copyleft conflict with GPL-3.0-or-later**, and the app's own
Python code is not affected (the libraries sit inside a separate
wheel). What it does create is an **obligation**: conveying those
binaries requires their licence texts and access to their source
(GPLv3 §6 `LICENSE:245-286`; LGPL-2.1 §4 / LGPL-3 §6 for the LGPL
libraries — section numbers per the standard texts, wording not re-read
in this session). The deb ships PyAV's BSD text only; no licence texts
for the 32 bundled libraries and no source offer were found
(`build-deb.sh` writes no `copyright`/notice file) → [G1](#4-gaps-to-close-before-a-paid-build).
This applies to the **free** deb as much as a paid one.

**OPEN** (O1, O2): x264's and x265's licence texts could not be fetched
(code.videolan.org and Debian sources returned bot challenges); the
above relies on FFmpeg's page naming libx264 as GPL. The banner/GPL
discrepancy (LGPLv3+ banner in a build that links x264/x265) should be
raised with PyAV or resolved by building without those codecs.

### 2.5 Model weights actually downloaded

None are shipped in the deb, Flatpak or wheel; the app downloads them at
first use (egress audit, `fluidvoice/model_download.py` row).

| Weights | Source the app uses | Upstream licence (HF API `cardData.license`) | Evidence |
|---|---|---|---|
| faster-whisper tiny/base/small/medium/large-v3 | `Systran/faster-whisper-*` (`fluidvoice/backends/__init__.py:32-36`) | `mit` | `https://huggingface.co/api/models/Systran/faster-whisper-small` (same for the others) |
| faster-whisper large-v3-turbo | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (`backends/__init__.py:37`) | `mit` | HF API |
| whisper.cpp ggml models | `ggerganov/whisper.cpp` (`fluidvoice/model_catalog.py:23-47`) | `mit` | HF API |
| Local-polish chat model | `unsloth/Qwen3-4B-Instruct-2507-GGUF`, Q4_K_M, sha256-pinned (`model_catalog.py:59-70`) | `apache-2.0`, `license_link` → Qwen's LICENSE | HF API (also `Qwen/Qwen3-4B-Instruct-2507`: `apache-2.0`) |
| Parakeet TDT v2/v3 int8 ONNX | `k2-fsa/sherpa-onnx` release tarballs (`model_catalog.py:87-122`) | source model `nvidia/parakeet-tdt-0.6b-v2` / `-v3`: `cc-by-4.0` | HF API. The sherpa-onnx export's own terms: **OPEN** (O3) |
| torch backend `.pt` files | `openai-whisper` package (`pyproject.toml:33`) | HF cards differ per model: `openai/whisper-small` = `apache-2.0`, `openai/whisper-large-v3-turbo` = `mit` | HF API → **OPEN** (O4) which text governs which artifact |
| `llama-server` binary for local polish | user-supplied package (`Recommends: llama.cpp`, `build-deb.sh:194`) | not verified here | — |

**If a paid offer bundles weights:** MIT and Apache-2.0 require keeping
the notices ("The above copyright notice and this permission notice
shall be included in all copies or substantial portions of the
Software", [MIT](https://github.com/spdx/license-list-data/blob/main/text/MIT.txt);
Apache-2.0 §4(a)/(c), [licence](https://www.apache.org/licenses/LICENSE-2.0));
CC-BY-4.0 requires attribution of the creator, notices, a link to the
material and an indication of modifications
([legal code §3(a)](https://creativecommons.org/licenses/by/4.0/legalcode)).
No share-alike, so weights can ship inside a GPL product.

## 3. Redistribution design options

Common requirements for **every** option that conveys the software:

- Licence text and notices with each copy (`LICENSE:195-206`, §4–§5).
- For anything containing object code (the deb): Corresponding Source
  "through the same place at no further charge" (§6(d), `LICENSE:275`).
- No further restrictions on the rights granted (§10, `LICENSE:463`):
  no clause forbidding resale, sharing or modification; no fee "for
  exercise of rights granted under this License" (`LICENSE:463-466`).
- Buyers may redistribute "with or without a fee"
  ([FAQ #DoesTheGPLRequireAvailabilityToPublic](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLRequireAvailabilityToPublic));
  the project must not require payment or notification when someone
  receives a copy from someone else
  ([FAQ #DoesTheGPLAllowRequireFee](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLAllowRequireFee)).
- Inherited prompts, rule tables and sounds stay GPLv3 in every
  build, paid or free; upstream's credit stays visible (`README.md:298-308`).
- No click-through is needed to receive or run a copy (§9,
  `LICENSE:435`); sale terms must never be a precondition of the GPL
  rights.

| Option | What the buyer pays for | Permitted? | REQUIRES | Must NOT | Fit with today's facts |
|---|---|---|---|---|---|
| **A. Plain GPL sale** (paid copy of the build) | The copy: a tested, signed build and convenient install | Yes — `LICENSE:205`; FAQ #DoesTheGPLAllowMoney, #DoesTheGPLAllowDownloadFee | Everything in the common list; the paid build's **exact** source published at the same place, free of charge | Restricting resale; withholding source from non-buyers beyond what §6 requires; promising exclusivity | Allowed but weak: the identical artifact is already public (`PRICING.md:21-22` "identical code"). Only works if the store build equals the public one, so one source drop covers both |
| **B. Support / updates offer** (the buyer pays for a service) | Priority support with a stated window, a defined update channel, maintenance | Yes — "you may offer support or warranty protection for a fee" (`LICENSE:205-206`); support/warranty may be conditioned on not having modified the program ([FAQ #v3ConditionalWarranty](https://www.gnu.org/licenses/gpl-faq.html#v3ConditionalWarranty)) | All of A for every conveyed build; terms that bind **the service only** (hours, response window, update term, machine count for support); source for a received version stays available after the term ends; the entitlement key never gates software (already true: `docs/guides/license.md` — "never gates the dictation loop", version check "does not disable anything"; `fluidvoice/license.py:1-6`) | "Lifetime updates / lifetime support" wording (plan lines 198–199); making a received build stop working when payment stops — "the requirement to keep paying a fee limits the user's ability to run the program. This is an additional requirement on top of the GPL" ([FAQ #SubscriptionFee](https://www.gnu.org/licenses/gpl-faq.html#SubscriptionFee)); DRM on the key (`ADR-0005:64-66`) | **The chosen SKU** (`ADR-0005:39-44`, `PRICING.md:19-28`): one-time purchase, defined support, no gated features. Consistent with the licence as implemented |
| **C. Donor model** (no copy is sold) | Nothing is bought; gifts fund development | Yes — the GPL does not oblige the author to charge or to give copies to anyone ([FAQ #DoesTheGPLRequireAvailabilityToPublic](https://www.gnu.org/licenses/gpl-faq.html#DoesTheGPLRequireAvailabilityToPublic)) | The same notices/source obligations on whatever is distributed, for free or for a price | Donor-only **features** (a private fork of inherited parts is unavailable, plan lines 164–166); promising donor-only access that cannot be kept once a donor redistributes | Lowest legal complexity, weakest evidence of income (the commercial note rates voluntary support as possibly insufficient) |
| ~~D. Proprietary edition / dual licence / restrictive EULA~~ | Gated features, device limits | **No, not for inherited parts** — upstream grants GPLv3 only (README "License"); §10 bars further restrictions | — | Relabelling, stripping the licence file, adding "no redistribution" terms to checkout or store text | Closed. The plan and ADR-0005 already exclude it |

**Add-on for A or B — offline bundle with model weights:** allowed with
the notices and attribution in [2.5](#25-model-weights-actually-downloaded);
resolve O3/O4 first.

**Add-on for B — hosted processing:** outside the GPL; keep it
separately priced or bring-your-own-key (plan lines 185–187). Not
assessed here.

## 4. Gaps to close before a paid build

Ordered by impact. None of these blocks research; G1–G3 should be fixed
before the store build ships, and they apply to the free channels today.

| # | Gap | Evidence | Action (owner; outside this note's scope) |
|---|---|---|---|
| G1 | The deb redistributes 32 native libraries (GPL/LGPL family) from the PyAV wheel with **no licence texts and no source offer** | [2.4](#24-native-code-inside-the-pinned-pyav-wheel); `build-deb.sh` writes no notice file | Ship a third-party notice + source-reference file in the deb and on the download pages, or switch to an `av` build without x264/x265 |
| G2 | Four pinned wheels ship no licence text (`ctranslate2`, `onnxruntime`, `flatbuffers`, `tokenizers`); MIT/Apache-2.0 require the notices with copies | [2.2](#22-the-deb--apt-channel--hash-locked-closure-26-wheels); MIT text, Apache-2.0 §4 | Generate the same third-party notice from `constraints.txt` |
| G3 | No written source-offer statement for binaries; the tag is pushed and the release created in the same job (`release-publish.yml:172-192`), but no page says "source of this deb = tag vX.Y" | `grep -ri "corresponding source\|written offer" docs packaging site README.md` hits only `ADR-0005:16` and the 2026-09-11 research note; `site/terms.html` links the licence text only | One line beside every deb download; for the store build, publish its exact source |
| G4 | Licence documentation errors: `flatpak-permissions.md:78-79` says python-xlib "GPL-2.0+" (wheel: `LGPLv2+`) and Pillow "HPND-CMU-MIT-style" (wheel: `MIT-CMU`; deb pin 10.2.0: `HPND`) | [2.3](#23-flatpak-channel) | Correct the two labels |
| G5 | No per-file licence notices (4 of 109 files) and no copyright line; FSF: a bare licence file "does not explicitly state that the code in the same repository may be used under the GNU GPL" ([FAQ #LicenseCopyOnly](https://www.gnu.org/licenses/gpl-faq.html#LicenseCopyOnly)) | `grep -rIl "SPDX\|GNU General Public License\|GPL" fluidvoice --include=*.py` → 4 | Add an SPDX line per file and a `COPYING`/notice naming both rightsholders (project contributors; FluidVoice authors for inherited files) |
| G6 | Brand assets without a licence record: 18 provider logos (third-party trademarks), 6 symbolic icons of unknown origin | [1.2](#12-the-tree-by-origin) | Record source and licence per file, or replace with text labels / own icons |
| G7 | The sale terms already say "a license never gates features" and the app keeps working after a refund (`site/terms.html`, `site/refund.html`), but they grant use/study/modify/redistribute for "the **free build**" only, and the update channel has no stated term ("Packaged builds with the update channel included") | `site/terms.html` ("What is sold", "The software itself") | State that the paid build carries the same GPL-3.0 rights (as `ADR-0005:52-54` and `docs/guides/license.md:49-51` already do); define the update term (plan lines 181–186) |
| G8 | AGENTS.md says "~300 inherited upstream branches"; the remote has 175 | [1.1](#11-repository-facts) | Update the number |

## 5. OPEN items

Anything not verified in this session. Format: what is unknown, how to
close it.

| # | Open question | How to close |
|---|---|---|
| O1 | Exact licence text of x264 and x265 in the bundled versions | Read `COPYING` in the x264 and x265 sources for the versions PyAV 18.1.0 vendors |
| O2 | Whether PyAV's FFmpeg is GPL by FFmpeg's rule (x264/x265 linked) despite its LGPLv3+ banner | Ask PyAV, or inspect its FFmpeg build script |
| O3 | Terms of the sherpa-onnx Parakeet exports (beyond the CC-BY-4.0 source model) | Check the release asset for a licence file and the export script header |
| O4 | Which licence text governs the torch backend's `.pt` files (HF cards disagree) | Read `openai/whisper` `LICENSE` and the model card of the exact weights used |
| O5 | Whether inherited files carry an "or later" grant | Ask upstream, or mark the port GPL-3.0-only in metadata |
| O6 | Trademark status of "FluidVoice", "SayItErmano" and the provider logos | Upstream trademark policy (none found in either repo); registry search |
| O7 | Origin of the six symbolic SVG icons | Compare with icon themes' path data; record provenance |
| O8 | Who created `FV_start_0.m4a` / `FV_end_0.m4a` (stock or commissioned) | Upstream history before `b395a7a` |
| O9 | Notices for translations, the GLM mark's font, the RFC 8032 reference code | Add a translator line to the `po` headers; keep the font's and IETF's licence text beside the outputs |
| O10 | Whether the GitHub fork network or the `fluidvoice` module name has consequences for a store listing | Decide with the owner; no licence impact found |
| O11 | Licence of the vendored `tools/factory/` tree | Check the SSSF/ADW upstream repositories before the next source-only distribution (AUR tarball, GitHub archive) |
| O12 | Licences of `Depends` system packages (not redistributed) | Low priority; list them if the store page promises "open-source stack" |
| O13 | Per-file authorship history (the task clone has one commit) | `git log --follow` on the prompt and sfx files in a full clone |
| O14 | Whether Paddle's seller-of-record terms (incorporated by `site/terms.html`, "Payments and taxes") sit cleanly beside the GPL statement; Flathub's listing rules for the AGPL dependency | Legal review of the checkout text and a Flathub policy read before launch |
| O15 | Individual licences of the other 30 bundled libraries (gnutls, nettle, hogweed, gmp, lame, unistring, alsa, …) | Read each project's `COPYING` for the vendored versions; expected LGPL/BSD/MIT families, unverified |

## Method and limits

- The task clone is shallow: `git rev-list --count HEAD` = 1 (`e22275b`).
  History-derived facts come from the GitHub API (repository metadata,
  contributors, branch list, upstream tree and README), not from local
  `git log`. No blame, no per-file import history (O13).
- Every tree claim was checked against files in the repo at `e22275b`;
  every licence label was read from the artifact that carries it
  (wheel `METADATA`, PyPI JSON at the pinned version, HF API), not from
  memory. FSF statements were read from gnu.org on 2026-11-22.
- Not done: legal review, trademark search, reading the upstream sources
  beyond the tree/README API, checking Flathub's own listing rules, or
  any checkout/Paddle terms. This is not legal advice.
- Three web sources refused automated reads (code.videolan.org,
  salsa.debian.org, sources.debian.org): x264/x265 texts stay OPEN (O1).

Re-verify in about two minutes:

```bash
gh api repos/acailic/SayItErmano --jq '{fork, parent: .parent.full_name, license: .license.spdx_id}'
gh api "repos/altic-dev/FluidVoice/git/trees/b395a7af0242b6869867abdd61e245a5a80ec218?recursive=1" \
   --jq '.tree[] | select(.path|test("Resources/FV_(start|end)_0.m4a")) | "\(.sha) \(.path)"'
git hash-object fluidvoice/assets/sfx/FV_start_0.m4a fluidvoice/assets/sfx/FV_end_0.m4a LICENSE
python3 -c "import zipfile,glob;[print(w, [n for n in zipfile.ZipFile(w).namelist() if n.endswith('METADATA')][0], *[l for l in zipfile.ZipFile(w).read([n for n in zipfile.ZipFile(w).namelist() if n.endswith('METADATA')][0]).decode().splitlines() if l.startswith('License')], sep='\n  ') for w in sorted(glob.glob('packaging/wheels/*.whl'))]"
curl -s https://huggingface.co/api/models/nvidia/parakeet-tdt-0.6b-v2 | jq .cardData.license
# PyAV wheel internals: download the pinned wheel, list av.libs/, strings libavcodec-* for "license"
```
