Named AI-polish provider profiles (e.g. "GLM") so the endpoint can be switched without retyping base_url/model/key-env — today only prompt-text profiles exist (fluidvoice/ai/profiles.py stores prompts, not providers).

STATUS: DRAFT

Design agreed 2026-11-10 (approach: sidecar store + switch-writes-config; hot-applies, no daemon changes to the polish path).

Scope:

1) Store `fluidvoice/ai/provider_profiles.py` (mirror ai/profiles.py discipline): `ai-profiles.json` beside config.toml; shape `{"<name>": {"base_url": str, "model": str, "api_key_env": str, "temperature": float?}}`; atomic 0600 writes; missing/corrupt → `{}` with one warning; names 1-64 chars. NEVER store api_key values — env-var names only (keeps the env/file-only rule; api_key itself stays unsettable via socket).

2) Config: new `_spec("ai", "profile", "", ...)` — active profile name, "" = custom/hand-edited. No [ai] key is RESTART_REQUIRED and AIClient is built per dictation (rewrite.py), so switching hot-applies. Switch = one set-config call writing ai.profile + the profile's base_url/model/api_key_env (+temperature when the profile carries one). Editing base_url/model by hand in Settings clears the pointer to "" (radio falls back to "Custom").

3) Templates: built-in dict (name → preset) offered via "Add from template" in Settings; selecting one COPIES it into the user store (built-ins are never "active" themselves). Set: GLM (https://api.z.ai/api/paas/v4, glm-4.7, ZAI_API_KEY), OpenAI (https://api.openai.com/v1, gpt-5, OPENAI_API_KEY), Groq (https://api.groq.com/openai/v1, llama-3.3-70b-versatile, GROQ_API_KEY), OpenRouter (https://openrouter.ai/api/v1, openrouter/auto, OPENROUTER_API_KEY), Ollama (http://localhost:11434/v1, empty model), LM Studio (http://localhost:1234/v1, empty model). Templates are editable strings, not commitments.

4) Settings → AI: "Provider profiles" PreferencesGroup ABOVE the endpoint fields — radio rows (active = ai.profile; extra "Custom" row when pointer is ""), per-row ⋯ menu (rename/delete), Add… entry row, Add-from-template dialog. Selecting a radio applies immediately via the existing socket set-config (same reactive flow as other instant-applied switches); rename/delete are store-file edits (app-side, prompt-profiles style). Deleting the active profile resets ai.profile to "" in the same set-config call; renaming it updates the pointer.

5) Tray: "AI profile" submenu listing store names with a checkmark on ai.profile; hidden when the store is empty. The tray handler applies the profile through the daemon's internal apply-config helper (same path the socket route uses), not by hand-editing config.toml.

6) Logos: logos.py PROVIDERS gains ("glm", ("z.ai", "bigmodel")); add assets/providers/glm.png + glm-light.png (monochrome mark, follows the existing white-recolor convention).

7) Tests (offline tier, mirror the prompt-profiles test file): store round-trip/corruption/name rules, template copy-on-add, switch writes pointer+values and clears on manual edit, tray submenu contents + apply, settings rows wiring, `ai.profile` spec coercion. No network.

8) Docs: docs/guides/configuration.md AI section gains provider profiles (switch semantics, env-only keys, GLM example).

Open item (resolve at live verify): whether the user's GLM Coding Plan token works on api.z.ai/api/paas/v4 — Settings "Test connection" answers it on first use; if not, a normal Z.ai API key is needed.

Out of scope: storing api_key values, per-app provider overrides, profile import/export, upstream-macOS parity sync, ollama model listing.
