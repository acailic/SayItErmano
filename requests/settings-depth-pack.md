Settings depth pack - three visible Settings gaps from the roadmap: user-editable prompt profiles (the last open v0.2 row, docs/ROADMAP.md "User-editable prompt profiles (named presets of the base prompt)"), per-model language selection (v0.4 row "Per-model language selection"), and model-manager pruning (v0.4 row "Model manager ... prune/freeing stays open"). All three have upstream references for semantics; this is additive UI + config plumbing over editors and stores that already exist.

STATUS: SHIPPED

<!-- shipped in 93e21b2 -->

Today: the AI base prompt is a single global string edited in Settings -> AI (gtkui/settings_window.py, config key ai.base_prompt); there is no way to save/switch variants. Model entries (model_catalog.py, model_download.py, Settings -> Models) have no per-model language and no disk-space management - stale model dirs in the models cache can only be removed by hand. Config round-trips go through the validated set-config socket path (control.py), which is the only write path new UI should use.

Scope:
1) Prompt profiles: named presets of the full AI prompt config subset (base prompt + any future keys stay out - exactly base_prompt for v1). CRUD: Save current as name, load, rename, delete; stored in a JSON beside the config (paths.py; e.g. prompt-profiles.json, shape {name: base_prompt}, 0600). Settings -> AI gains a profile bar above the prompt editor (dropdown + save-as entry + delete, confirmation on delete). No active-profile pointer - loading copies text into the editor; what is saved in config.toml remains the single source of truth.
2) Per-model language: each model store gains an optional language key (whisper family: passes --language / language= to the backend where supported; verify exact plumbing per backend in fluidvoice/backends/ during planning; empty = auto-detect, current behavior). Surfaces: Settings -> Models per-model language entry; config example comment; doctor reports resolution.
3) Model pruning: Settings -> Models gains a disk-usage section - per cached model dir size + total, delete button per non-active model (confirmation dialog; active model disabled with tooltip), implemented over a new socket action (control.py, e.g. model-delete with path validation confined to the models cache root) - the GTK app never deletes files directly; doctor gains the models line if missing.
4) Tests: unit - profiles load/save/rename/delete round-trip + malformed file tolerance; language key plumbed per backend (mock backend assertions); model-delete action validates path confinement + refuses active model. gtkui test pattern follows tests/test_gtkui.py (rows render, actions post to socket).

Where: fluidvoice/gtkui/settings_window.py (AI profile bar, Models language + pruning sections), fluidvoice/paths.py (profiles file), fluidvoice/control.py (model-delete action), fluidvoice/model_catalog.py + model_download.py (sizes, language plumbing), fluidvoice/backends/ (language arg), fluidvoice/doctor.py, tests/test_settings_profiles.py (new), tests/test_models_manager.py (new or extended existing).

Done means: a phased plan under specs/ where each phase leaves `.venv/bin/python -m pytest -q tests --ignore=tests/integration` green; profiles survive restart and a malformed profiles file degrades to an empty list with one WARN, never a crash; setting a language on a whisper model verifiably reaches the backend invocation; model-delete removes a decoy cached model dir via the socket and refuses both the active model and any path outside the cache root.

Out of scope: cloud prompt libraries, prompt versioning/diffing, automatic language detection per dictation, auto-pruning policies (age/size thresholds), downloading new model types, any UI beyond the three sections named.
