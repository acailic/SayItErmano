"""Models page: catalog rows, GGUF + Parakeet downloads, disk usage, engine options.Mixin methods are defined on SettingsWindow; this split is structural only."""

from __future__ import annotations

import threading

from gi.repository import Adw, GLib, Gtk

from ... import backends, model_catalog, model_download
from ...config import KNOWN_LANGUAGES as LANGUAGES
from ...config import ui_range
from ..client import ClientError
from .common import _ListProxy, _PasswordProxy


class ModelsPageMixin:
    def _build_models(self) -> None:
        page = Adw.PreferencesPage(
            name="models", icon_name="fluidvoice-models-symbolic", title="Models"
        )
        self.models_group = Adw.PreferencesGroup(
            title="Speech models",
            description="faster-whisper models (downloaded on first use)",
        )
        page.add(self.models_group)

        self.gguf_group = Adw.PreferencesGroup(
            title="whisper.cpp GGUF",
            description="Direct ggml models for the whisper.cpp backend",
        )
        page.add(self.gguf_group)

        self.parakeet_group = Adw.PreferencesGroup(
            title="Parakeet (ONNX)",
            description="NVIDIA Parakeet TDT via ONNX Runtime — explicit backend",
        )
        page.add(self.parakeet_group)

        warm = Adw.PreferencesGroup(title="State")
        self.warmup_row = Adw.ActionRow(title="Model", subtitle="—")
        self.warmup_spinner = Gtk.Spinner()
        self.warmup_row.add_suffix(self.warmup_spinner)
        warm.add(self.warmup_row)
        page.add(warm)

        # Memory: idle model unload (model.idle_unload_s). Whole minutes in
        # the UI (0 = off); the config stores seconds - see _load/_collect.
        mem = Adw.PreferencesGroup(
            title="Memory",
            description="Free RAM/VRAM between dictations (applies live)",
        )
        # 0..1440 minutes derives from the registry's 0..86400 seconds.
        _idle_bounds = ui_range("model", "idle_unload_s") or (0, 86400)
        adj = Gtk.Adjustment(
            value=0, lower=0, upper=_idle_bounds[1] // 60, step_increment=1
        )
        self._idle_unload_row = Adw.SpinRow(
            title="Unload model after idle (minutes)",
            subtitle="0 = keep the model loaded (fastest first word); "
            "higher frees RAM/VRAM after inactivity — the next "
            "dictation pays the model load time",
            adjustment=adj,
            digits=0,
        )
        self._idle_unload_row.connect("notify::value", lambda *_: self._touch())
        mem.add(self._idle_unload_row)
        page.add(mem)

        engine = Adw.PreferencesGroup(
            title="Engine options", description="Changing these reloads the model"
        )
        engine.add(
            self._combo(
                "model",
                "backend",
                "Backend",
            )
        )
        engine.add(
            self._combo(
                "model",
                "device",
                "Device",
            )
        )
        engine.add(
            self._combo(
                "model",
                "compute",
                "Compute",
            )
        )
        engine.add(
            self._entry(
                "model",
                "whispercpp_model",
                "whisper.cpp model — name like ggml-base.bin, or a path",
            )
        )
        engine.add(
            self._switch(
                "model",
                "eager_warmup",
                "Load at startup",
                "Warm the model when the daemon starts (needs a daemon restart)",
            )
        )
        page.add(engine)

        # Remote OpenAI-compatible STT server: local-first — everything is
        # off while the URL is empty; audio goes only to the URL you set.
        remote = Adw.PreferencesGroup(
            title="Remote (OpenAI-compatible)",
            description="Dictation POSTs the recorded WAV to "
            "<url>/v1/audio/transcriptions — e.g. "
            "http://lan-box:8000 (vLLM / whisper.cpp server / "
            "NIM / DGX Spark). Empty URL = local models only; "
            "audio leaves this machine only toward this URL",
        )
        remote.add(self._entry("model", "remote_url", "Server URL"))
        remote.add(self._entry("model", "remote_model", "Model name"))
        key_row = Adw.ActionRow(
            title="API key", subtitle="optional bearer token (never shown)"
        )
        key_entry = Gtk.Entry(
            visibility=False,
            hexpand=True,
            valign=Gtk.Align.CENTER,
            input_purpose=Gtk.InputPurpose.PASSWORD,
        )
        key_entry.connect("changed", lambda *_: self._touch())
        key_row.add_suffix(key_entry)
        remote.add(key_row)
        self._rows[("model", "remote_api_key")] = _PasswordProxy(key_entry, key_row)
        remote.add(
            self._spin(
                "model",
                "remote_timeout_s",
                "Timeout (seconds)",
                1,
                digits=0,
                subtitle="per-request; retry once on transient network errors",
            )
        )
        page.add(remote)

        hot = Adw.EntryRow(title="Hotwords (comma-separated)")
        self._rows[("model", "hotwords")] = _ListProxy(hot)
        hot.connect("changed", lambda *_: self._touch())
        hot_group = Adw.PreferencesGroup(
            title="Vocabulary boosting",
            description="Words the decoder is biased toward (ADD, not "
            "replace) — fed as faster-whisper hotwords / "
            "whisper initial-prompt; changing them reloads "
            "the engine",
        )
        hot_group.add(hot)
        page.add(hot_group)

        self.lang_overrides_group = Adw.PreferencesGroup(
            title="Per-model language",
            description="Overrides general.language per model - "
            "empty = inherit, auto = always detect",
        )
        page.add(self.lang_overrides_group)

        self.disk_group = Adw.PreferencesGroup(
            title="Disk usage",
            description="Cached models under ~/.cache/sayit-ermano/models "
            "(deletion needs the daemon)",
        )
        self.disk_total_row = Adw.ActionRow(title="Total", subtitle="—")
        self.disk_group.add(self.disk_total_row)
        self._disk_rows: list[Adw.ActionRow] = []
        page.add(self.disk_group)
        page.add(self._save_group())
        self._add_page(page)

    def _refresh_models(self) -> None:
        for row in self._model_rows:
            self.models_group.remove(row)
        self._model_rows = []
        active = self._active_model()
        for name, info in model_catalog.MODEL_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=(
                    f"{info['size']} · {info['langs']} languages · {info['note']}"
                ),
            )
            if name == active:
                # macOS parity: the default model reads as a radio choice
                radio = Gtk.CheckButton()
                radio.set_active(True)
                radio.set_sensitive(False)
                radio.set_valign(Gtk.Align.CENTER)
                radio.set_tooltip_text("Current default model")
                row.add_prefix(radio)
                row.add_suffix(
                    Gtk.Label(label="Active", css_classes=["success", "caption"])
                )
            else:
                downloaded = model_catalog.model_downloaded(name)
                btn = Gtk.Button(
                    label="Use" if downloaded else "Download & use",
                    css_classes=["suggested-action"],
                )
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._pick_model, name)
                row.add_suffix(btn)
            self.models_group.add(row)
            self._model_rows.append(row)
        self._refresh_gguf_rows()
        self._refresh_parakeet_rows()
        self._refresh_model_language_rows()
        self._refresh_disk_rows()
        st = self.c.status() or {}
        warm = st.get("warmup") or {}
        if warm.get("running"):
            self.warmup_spinner.start()
            self.warmup_row.set_subtitle(f"loading {warm.get('model') or ''}…")
        elif warm.get("error"):
            self.warmup_row.set_subtitle(f"error: {warm['error']}")
        else:
            self.warmup_spinner.stop()
            rurl = str(
                (self.cfg.get("model", {}) or {}).get("remote_url") or ""
            ).strip()
            if rurl:
                from urllib.parse import urlparse

                rmodel = str(
                    (self.cfg.get("model", {}) or {}).get("remote_model") or "-"
                )
                host = urlparse(rurl).netloc or rurl
                self.warmup_row.set_subtitle(f"active: remote ({rmodel} @ {host})")
            else:
                self.warmup_row.set_subtitle(f"active: {active or '—'}")
        if not (st.get("model_state") or {}).get("loaded", True):
            self.warmup_row.set_subtitle(
                "unloaded (idle — reloads on the next dictation)"
            )
        if st:  # about rows (daemon offline keeps the placeholder em-dash)
            self.about_backend_row.set_subtitle(st.get("backend") or "—")
            self.about_gpu_row.set_subtitle("yes" if st.get("cuda") else "no")
            self._apply_about_update(st)

    def _active_model(self) -> str:
        name = str(self.cfg.get("model", {}).get("name", "auto"))
        if name in ("", "auto"):
            return backends.resolve_model_name(name)
        return backends.ALIASES.get(name.lower(), name.lower())

    def _apply_about_update(self, st: dict) -> None:
        """About → Update row from the daemon status payload (no network
        here - the daemon's checker owns the probe; see update.py)."""
        u = st.get("update") or {}
        if st.get("update_available"):
            self.about_update_row.set_subtitle(
                f"v{st['update_available']} available — run 'sayit-ermano update'"
            )
        elif not u.get("enabled"):
            self.about_update_row.set_subtitle("checks disabled")
        elif u.get("checked"):
            self.about_update_row.set_subtitle("up to date")
        else:
            self.about_update_row.set_subtitle("checking…")

    def _pick_model(self, btn, name: str) -> None:
        try:
            resp = self.c.select_model(name)
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("ok") is False:
            self.toast(str(resp.get("error") or "failed"))
            return
        self.toast(f"Switching to {name}…")
        GLib.timeout_add_seconds(1, self._poll_model)

    def _poll_model(self) -> bool:
        self.cfg, self._from_daemon = self.c.get_config()
        self._refresh_models()
        warm = (self.c.status() or {}).get("warmup") or {}
        if warm.get("running"):
            return True
        if warm.get("error"):
            self.toast(f"model error: {warm['error']}")
        return False

    def _downloaded_model_names(self) -> list[str]:
        """Every downloaded model across the three catalogs. Parakeet v2
        (English-only) has no language to constrain and is skipped."""
        names = [
            n for n in model_catalog.MODEL_CATALOG if model_catalog.model_downloaded(n)
        ]
        names += [
            n for n in model_catalog.GGUF_CATALOG if model_catalog.gguf_downloaded(n)
        ]
        names += [
            n
            for n in model_catalog.PARAKEET_CATALOG
            if n != "parakeet-tdt-0.6b-v2" and model_catalog.parakeet_downloaded(n)
        ]
        return names

    def _model_lang_values(self, saved: str) -> list[tuple[str, str]]:
        values = [("inherit (general)", ""), ("auto (detect)", "auto")] + [
            (c, c) for c in LANGUAGES
        ]
        if saved and saved != "auto" and saved not in LANGUAGES:
            values.append((f"{saved} (saved)", saved))  # unknown saved code
        return values

    def _set_model_lang_selection(self, name: str) -> None:
        saved = str((self.cfg.get("model", {}).get("languages") or {}).get(name, ""))
        values = self._model_lang_values(saved)
        model = Gtk.StringList()
        for label, _v in values:
            model.append(label)
        row = self._model_lang_rows[name]
        self._suppress_touch = True  # programmatic rebuild: no dirty
        try:
            row.set_model(model)
            row.set_title(name)
            row.set_selected([v for _l, v in values].index(saved))
        finally:
            self._suppress_touch = False
        self._model_lang_values_map[name] = values

    def _refresh_model_language_rows(self, reset: bool = False) -> None:
        """Diff the downloaded set against the rows and add/remove only the
        diff (entered selections survive refreshes; reset re-syncs them
        from cfg, e.g. on Discard). Mic-priority discipline."""
        wanted = self._downloaded_model_names()
        for name in [n for n in list(self._model_lang_rows) if n not in wanted]:
            self.lang_overrides_group.remove(self._model_lang_rows.pop(name))
            self._model_lang_values_map.pop(name, None)
        for name in wanted:
            if name not in self._model_lang_rows:
                row = Adw.ComboRow(title=name)
                row.connect("notify::selected", self._lang_row_touch)
                self._model_lang_rows[name] = row
                self._model_lang_values_map[name] = []
                self._set_model_lang_selection(name)
                self.lang_overrides_group.add(row)
            elif reset:
                self._set_model_lang_selection(name)
        self.lang_overrides_group.set_visible(bool(self._model_lang_rows))

    def _lang_row_touch(self, *_args) -> None:
        if not self._suppress_touch:
            self._touch()

    def _collect_model_languages(self) -> dict[str, str]:
        """model.languages from the shown rows; entries for models WITHOUT
        rows (no longer downloaded) are carried over from cfg unchanged."""
        out: dict[str, str] = {}
        for name, row in self._model_lang_rows.items():
            values = self._model_lang_values_map.get(name) or []
            idx = row.get_selected()
            code = values[idx][1] if 0 <= idx < len(values) else ""
            if code:  # ""/inherit drops the key
                out[name] = code
        shown = set(self._model_lang_rows)
        for k, v in (self.cfg.get("model", {}).get("languages") or {}).items():
            if k not in shown and v:
                out[str(k)] = str(v)
        return out

    def _refresh_disk_rows(self) -> None:
        """Per-cached-model size rows + total (direct read, dict_suggestions
        precedent); deletion itself goes through the daemon."""
        for row in self._disk_rows:
            self.disk_group.remove(row)
        self._disk_rows = []
        try:
            entries = model_catalog.cached_models()
        except Exception:
            entries = []  # a cache listing failure must not break the page
        total = sum(e["bytes"] for e in entries)
        self.disk_total_row.set_subtitle(
            f"{len(entries)} model{'' if len(entries) == 1 else 's'} · "
            f"{model_catalog.human_bytes(total)}"
            if entries
            else "empty"
        )
        active = self._active_model() or self._active_gguf() or self._active_parakeet()
        for e in entries:
            row = Adw.ActionRow(
                title=e["name"],
                subtitle=f"{e['kind']} · "
                f"{model_catalog.human_bytes(e['bytes'])} · {e['path']}",
            )
            btn = Gtk.Button(label="Delete", css_classes=["flat", "destructive-action"])
            btn.set_valign(Gtk.Align.CENTER)
            if e["name"] == active:
                btn.set_sensitive(False)
                btn.set_tooltip_text("This is the active model")
            else:
                btn.connect("clicked", self._confirm_delete_model, e)
            row.add_suffix(btn)
            self.disk_group.add(row)
            self._disk_rows.append(row)

    def _confirm_delete_model(self, _btn, entry: dict) -> None:
        dlg = Adw.MessageDialog(
            transient_for=self,
            modal=True,
            heading=f"Delete {entry['name']}?",
            body=f"Frees {model_catalog.human_bytes(entry['bytes'])} from "
            "the models cache.",
        )
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("delete", "Delete")
        dlg.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", self._on_delete_model_response, entry)
        dlg.present()

    def _on_delete_model_response(self, _dlg, response, entry: dict) -> None:
        if response != "delete":
            return
        kind, name = entry["kind"], entry["name"]

        def work():
            try:
                resp = self.c.model_delete(kind, name)
            except ClientError:
                resp = {
                    "ok": False,
                    "error": "daemon not running - start it to delete models",
                }
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                resp = {"ok": False, "error": str(e)}
            GLib.idle_add(self._after_model_delete, name, resp)

        threading.Thread(target=work, daemon=True).start()

    def _after_model_delete(self, name: str, resp: dict) -> None:
        if resp.get("ok"):
            freed = model_catalog.human_bytes(int(resp.get("bytes") or 0))
            self.toast(f"Deleted {name} (freed {freed})")
        else:
            self.toast(str(resp.get("error") or "delete failed"))
        self._refresh_models()

    def _active_gguf(self) -> str | None:
        m = self.cfg.get("model", {})
        if str(m.get("backend", "")) != "whisper.cpp":
            return None
        val = str(m.get("whispercpp_model", "")).strip()
        return val if val in model_catalog.GGUF_CATALOG else None

    def _dl_subtitle(self, name: str) -> str:
        st = self._gguf_dl.get(name) or {}
        b, t = st.get("bytes", 0), st.get("total")

        def mb(n):
            return f"{n / 1_000_000:.0f} MB"

        return f"downloading… {mb(b)} / {mb(t)}" if t else f"downloading… {mb(b)}"

    def _refresh_gguf_rows(self) -> None:
        for row in self._gguf_rows:
            self.gguf_group.remove(row)
        self._gguf_rows = []
        active = self._active_gguf()
        for name, info in model_catalog.GGUF_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=(
                    f"{info['size']} · {info['langs']} languages · {info['note']}"
                ),
            )
            if name == active:
                # macOS parity: the default model reads as a radio choice
                radio = Gtk.CheckButton()
                radio.set_active(True)
                radio.set_sensitive(False)
                radio.set_valign(Gtk.Align.CENTER)
                radio.set_tooltip_text("Current default model")
                row.add_prefix(radio)
                row.add_suffix(
                    Gtk.Label(label="Active", css_classes=["success", "caption"])
                )
            elif (
                name in self._gguf_dl
                and not self._gguf_dl[name].get("done")
                and not self._gguf_dl[name].get("error")
            ):
                row.set_subtitle(self._dl_subtitle(name))
                row.add_suffix(Gtk.Spinner(spinning=True))
            elif model_catalog.gguf_downloaded(name):
                btn = Gtk.Button(label="Use", css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._use_gguf, name)
                row.add_suffix(btn)
            else:
                btn = Gtk.Button(
                    label="Download & use", css_classes=["suggested-action"]
                )
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._download_gguf, name)
                row.add_suffix(btn)
            self.gguf_group.add(row)
            self._gguf_rows.append(row)

    def _download_gguf(self, _btn, name: str) -> None:
        if name in self._gguf_dl and not (
            self._gguf_dl[name].get("done") or self._gguf_dl[name].get("error")
        ):
            return  # already running
        self._gguf_dl[name] = {"bytes": 0, "total": None, "done": False, "error": None}
        self._refresh_models()

        def work():
            st = self._gguf_dl[name]
            try:
                model_download.download_gguf(
                    name, progress=lambda b, t: st.update(bytes=b, total=t)
                )
                st["done"] = True
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                st["error"] = str(e)[:300]

        threading.Thread(target=work, daemon=True).start()
        GLib.timeout_add(400, self._poll_gguf_dl, name)

    def _poll_gguf_dl(self, name: str) -> bool:
        st = self._gguf_dl.get(name)
        if st is None or not (st.get("done") or st.get("error")):
            # still running: rebuild rows for a live subtitle (download state
            # survives because it lives in self._gguf_dl, not the rows)
            self._refresh_models()
            return True
        self._refresh_models()
        if st.get("error"):
            self.toast(f"download failed: {st['error']}")
        else:
            self.toast(f"{name} downloaded — click Use to switch")
        return False

    def _use_gguf(self, _btn, name: str) -> None:
        if not model_catalog.gguf_downloaded(name):
            self.toast(f"{name} is not downloaded yet")
            return
        try:
            resp = self.c.set_config(
                {"model": {"backend": "whisper.cpp", "whispercpp_model": name}}
            )
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("rejected") or resp.get("errors"):
            self.toast(
                "Rejected: "
                + ", ".join((resp.get("rejected") or []) + (resp.get("errors") or []))
            )
            return
        self.toast(f"Switching to whisper.cpp ({name})…")
        self._load()  # resync cfg + rows
        GLib.timeout_add_seconds(1, self._poll_model)

    def _active_parakeet(self) -> str | None:
        m = self.cfg.get("model", {})
        if str(m.get("backend", "")) != "parakeet":
            return None
        name = str(m.get("name", "")).strip()
        return name if name in model_catalog.PARAKEET_CATALOG else None

    def _parakeet_dl_subtitle(self, name: str) -> str:
        st = self._parakeet_dl.get(name) or {}
        b, t = st.get("bytes", 0), st.get("total")

        def mb(n):
            return f"{n / 1_000_000:.0f} MB"

        return f"downloading… {mb(b)} / {mb(t)}" if t else f"downloading… {mb(b)}"

    def _refresh_parakeet_rows(self) -> None:
        for row in self._parakeet_rows:
            self.parakeet_group.remove(row)
        self._parakeet_rows = []
        active = self._active_parakeet()
        for name, info in model_catalog.PARAKEET_CATALOG.items():
            row = Adw.ActionRow(
                title=name,
                subtitle=f"{info['size']} · {info['langs']} · {info['note']}",
            )
            if name == active:
                # macOS parity: the default model reads as a radio choice
                radio = Gtk.CheckButton()
                radio.set_active(True)
                radio.set_sensitive(False)
                radio.set_valign(Gtk.Align.CENTER)
                radio.set_tooltip_text("Current default model")
                row.add_prefix(radio)
                row.add_suffix(
                    Gtk.Label(label="Active", css_classes=["success", "caption"])
                )
            elif (
                name in self._parakeet_dl
                and not self._parakeet_dl[name].get("done")
                and not self._parakeet_dl[name].get("error")
            ):
                row.set_subtitle(self._parakeet_dl_subtitle(name))
                row.add_suffix(Gtk.Spinner(spinning=True))
            elif model_catalog.parakeet_downloaded(name):
                btn = Gtk.Button(label="Use", css_classes=["suggested-action"])
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._use_parakeet, name)
                row.add_suffix(btn)
            else:
                btn = Gtk.Button(
                    label="Download & use", css_classes=["suggested-action"]
                )
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", self._download_parakeet, name)
                row.add_suffix(btn)
            self.parakeet_group.add(row)
            self._parakeet_rows.append(row)

    def _download_parakeet(self, _btn, name: str) -> None:
        if name in self._parakeet_dl and not (
            self._parakeet_dl[name].get("done") or self._parakeet_dl[name].get("error")
        ):
            return  # already running
        self._parakeet_dl[name] = {
            "bytes": 0,
            "total": None,
            "done": False,
            "error": None,
        }
        self._refresh_models()

        def work():
            st = self._parakeet_dl[name]
            try:
                model_download.download_parakeet(
                    name, progress=lambda b, t: st.update(bytes=b, total=t)
                )
                st["done"] = True
            except Exception as e:  # noqa: BLE001 - surfaced as a toast
                st["error"] = str(e)[:300]

        threading.Thread(target=work, daemon=True).start()
        GLib.timeout_add(400, self._poll_parakeet_dl, name)

    def _poll_parakeet_dl(self, name: str) -> bool:
        st = self._parakeet_dl.get(name)
        if st is None or not (st.get("done") or st.get("error")):
            # still running: rebuild rows for a live subtitle
            self._refresh_models()
            return True
        self._refresh_models()
        if st.get("error"):
            self.toast(f"download failed: {st['error']}")
        else:
            self.toast(f"{name} downloaded — click Use to switch")
        return False

    def _use_parakeet(self, _btn, name: str) -> None:
        if not model_catalog.parakeet_downloaded(name):
            self.toast(f"{name} is not downloaded yet")
            return
        try:
            resp = self.c.set_config({"model": {"backend": "parakeet", "name": name}})
        except Exception as e:
            self.toast(str(e))
            return
        if resp.get("rejected") or resp.get("errors"):
            self.toast(
                "Rejected: "
                + ", ".join((resp.get("rejected") or []) + (resp.get("errors") or []))
            )
            return
        self.toast(f"Switching to parakeet ({name})…")
        self._load()  # resync cfg + rows
        GLib.timeout_add_seconds(1, self._poll_model)
