UI science uplift — make the pill and history feel measurably better, grounded in the verified HCI literature collected in docs/research/ui-hci-papers.md (33 verified sources; section numbers below refer to it). Research-derived, not upstream-parity work: every change must keep the upstream look (black stadium, mode accents, Mac choreography) while fixing the moments the literature says matter most.

STATUS: SHIPPED

<!-- shipped in 4bc9e14 + d1805b6 (+71495e3) -->

Phase 1 — Pill lifecycle motion (fluidvoice/overlay.py, daemon.py wiring; tests in tests/test_overlay.py):
- Fade-IN on appear, symmetric to the existing `_fade_out` (overlay.py:871): ramp alpha 0→1 over ~4 frames (~130 ms at FPS=30) and start the waveform immediately. Research §1/§6: state change must land inside the 0.1 s "instant" band (Card 1991) and transitions at 100-300 ms (practitioner convention, consistent with Card/Miller — cite as convention). Today the window pops into existence on first blit.
- Success beat ("done" moment): today `_close_closing_display` (daemon.py:962) fades the pill out right after insert — the interaction ends on a vanish. Add an overlay `done` state rendered by PillRenderer: badge "✓" (reuse the send-badge slot, `set_badge`) + accent-green tint hold ~400 ms, then the existing fade. Research §7 peak-end (Kahneman 1993): the insert beat is the peak-end of every dictation cycle and is where polish budget pays. Upstream parity preserved: the done frame reuses the exact pill geometry, only badge+accent change.
- Reduced motion: read `org.gnome.desktop.interface enable-animations` once at overlay creation (gsettings subprocess; env override SAYITERMANO_NO_ANIMATIONS=0/1 wins, tests use it). When off: skip fades (instant map/unmap), render processing as constant mid-alpha flat bars (no shimmer sweep), no pulse. Research §8 (WCAG 2.3.3; Fulvio 2021 — peripheral motion is the risky kind, and this pill IS peripheral). Keep labels — state must never be motion-only.

Phase 2 — Processing honesty (overlay.py renderer only):
- Elapsed-time + still-working cue: FluidOverlay already tracks `_state_since` (overlay.py:838, currently unused by rendering). After ~2 s in processing, render the label as "Transcribing · 3 s"; at ≥4 s add a slow label alpha pulse. Research §1 (Miller 1968 bands; Maslych 2025 CUI: users prefer a working signal over silence, and ≥4 s needs an explicit still-alive cue). Cheap: one render-path branch + sig field.
- Audit every processing path sets state=processing (the never-dead-pill rule, research §1 Abbas 2022 / §2 Maister): dictate/rewrite do; verify cancel/error paths close rather than strand (PROCESSING_CAP already bounds hangs).

Phase 3 — Correction + confidence (the functional gap; history.py, backends, main_window.py):
- Confidence plumbing: faster-whisper segments expose avg_logprob/no_speech_prob (fluidvoice/backends + processing — check what `transcribe` already returns; thread a row-level 3-band ordinal confidence into history entries). Research §5 (Lee & See 2004; Antifakos 2004/2005): display honest ordinal confidence — 1-3 dots on the history row meta line and a tint on the pill done-badge. Never fake it; if a backend lacks confidence, omit the dots.
- Inline repair: make history rows click-to-edit (text label → editable on demand, save back to the JSONL) + "Insert at cursor" row action via the existing insertion path (daemon socket action or `insertion.insert_text` direct — follow how paste-last does it). Research §4 (Suhm 2001 TOCHI: re-dictation is the empirically slower repair path; Karat 1999: uncorrected errors propagate silently). Today repair = re-record or manual copy-edit elsewhere.
- Explicitly OUT until a backend exposes alternates: n-best per-segment pick lists (Murad 2019: only worth it at high WER; faster-whisper gives no n-best without custom beam plumbing).

Phase 4 — History scannability (main_window.py, HistoryEntryRow):
- Date headers ("Today"/"Yesterday"/date) grouping the ListBox instead of a flat wall of identical cards; keep cards for entries. Research §8 glanceability (Matthews 2006): structure so one glance separates recency buckets; Mejtoft 2018 §2 for structure-reads-as-faster.
- Mode chip as icon (mic/pen/terminal glyph) at the meta line start; "AI polished" already exists — keep copy verbatim.

Phase 5 — Trust distinction at insert:
- When rewrite mode lands, the done badge reads "✓ AI" vs dictate's plain "✓" (research §5: never conflate verbatim transcription with LLM output — conflating miscalibrates trust in both). History already marks mode; the pill moment is the gap.
- Tray already mirrors recording state with red icon + tooltip (tray.py:479) — no change; noted as the one research recommendation already satisfied.

Explicitly out: streaming rewrite text (needs AIClient transport streaming — big; note as future), notch UI, mascot/character art (keep the abstract pill; Mori uncanny-valley §7 — stylized-abstract only), changing upstream mode accents or pill geometry, Wayland, TCP/HTTP surfaces (locked scope per roadmap).

Done means: phased file-level plan under specs/ where each phase leaves `pytest tests` green; headless render tests in tests/test_overlay.py for fade-in frame count, done-state frame, elapsed label at t+2s/t+4s, and reduced-motion static path (SAYITERMANO_NO_ANIMATIONS=1); history tests for date grouping, confidence dots (present/absent by backend), inline edit round-trip through the JSONL; a config test for the animations setting plumbing.
