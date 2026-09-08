"""Data client for the GTK app: daemon actions over the unix control
socket, direct reads through the shared Python modules.

Degraded mode (daemon not running) per the native-app spec: history stays
fully usable, settings fall back to file-only mode (validate + save, apply
on next daemon start) and every daemon-backed call reports the reason.
No GTK imports here, so tests and headless tooling can use it too.
"""
from __future__ import annotations

from typing import Any

from .. import control
from .. import history as history_mod
from ..config import (
    RESTART_REQUIRED,
    apply_settings,
    load_config,
    mask_secrets,
    save_config,
)


class ClientError(RuntimeError):
    pass


class Client:
    """One place that knows where each piece of data comes from."""

    def __init__(self) -> None:
        self.daemon_down_reason: str | None = None  # set by the last failure

    # -- socket actions -------------------------------------------------------

    def _request(self, action: str, **kwargs) -> dict:
        try:
            resp = control.request(action, **kwargs)
        except control.ControlError as e:
            self.daemon_down_reason = str(e)
            raise ClientError(str(e)) from e
        self.daemon_down_reason = None
        if resp.get("ok") is False and resp.get("error"):
            raise ClientError(str(resp["error"]))
        return resp

    def status(self) -> dict | None:
        try:
            return self._request("status")
        except ClientError:
            return None

    def daemon_alive(self) -> bool:
        return self.status() is not None

    def toggle(self) -> dict:
        return self._request("toggle")

    def test_dictation(self, seconds: float = 3.0) -> dict:
        return self._request("test-dictation", seconds=seconds)

    def insert_text(self, text: str) -> dict:
        """Type `text` into the focused app via the daemon (history-window
        repair path). Raises ClientError when the daemon is down/busy."""
        return self._request("insert-text", text=text)

    def command_rerun(self, command: str,
                      purpose: str | None = None) -> dict:
        """Re-post a stored command as a PENDING proposal on the daemon -
        the daemon NEVER executes it without the hotkey confirm (the same
        two-press strong-confirm flow as a destructive voice command).
        Raises ClientError when the daemon is down/busy/unready."""
        return self._request("command-rerun", command=command,
                             purpose=purpose)

    def select_model(self, name: str) -> dict:
        return self._request("select-model", name=name)

    def model_delete(self, kind: str, name: str) -> dict:
        """Socket-only model-cache deletion (the GTK app never deletes
        files, not even in daemon-offline degraded mode). Raises
        ClientError when the daemon is down."""
        return self._request("model-delete", kind=kind, name=name)

    def mics(self) -> list[dict]:
        """Microphone list (socket action; direct tray query if down)."""
        try:
            return self._request("mics").get("mics") or []
        except ClientError:
            from ..tray import list_microphones
            try:
                return list_microphones()
            except Exception:
                return []

    # -- config ----------------------------------------------------------------

    def get_config(self) -> tuple[dict, bool]:
        """(config, from_daemon). Falls back to the file when the daemon is
        down - the settings window then runs in file-only mode."""
        try:
            resp = self._request("get-config")
            return resp.get("config") or load_config(), True
        except ClientError:
            return load_config(), False

    def set_config(self, body: dict) -> dict:
        """Save settings through the daemon (live cfg + apply) or, when it
        is not running, validate + write the file directly."""
        try:
            return self._request("set-config", config=body)
        except ClientError:
            cfg = load_config()
            changed, rejected = apply_settings(cfg, body)
            save_config(cfg)
            restart = [k for k in changed if k in RESTART_REQUIRED]
            note = ("daemon not running - saved to file, applies on next "
                    "daemon start")
            return {"ok": not rejected, "changed": changed,
                    "rejected": rejected, "restart_required": restart,
                    "applied": [], "errors": [], "note": note}

    def masked_config(self) -> dict:
        return mask_secrets(load_config())

    def test_ai(self, base_url: str, model: str) -> dict:
        """Ping the configured endpoint. Never attaches the stored/env API
        key to a host the user has not saved (same rule as the web UI)."""
        from urllib.parse import urlparse

        from ..ai.client import AIClient, AIError
        cfg = load_config()
        ai = dict(cfg.get("ai", {}), base_url=base_url, model=model,
                  enabled=True)
        tested = (base_url or "").rstrip("/")
        saved = (cfg.get("ai", {}).get("base_url") or "").rstrip("/")
        if urlparse(tested).hostname != urlparse(saved).hostname:
            ai["api_key"] = ""
            ai["api_key_env"] = ""
        cfg["ai"] = ai
        client = AIClient(cfg)
        if not client.configured:
            return {"ok": False, "error": "set base URL and model first"}
        try:
            return {"ok": True, "reply": client.chat("Reply with exactly: ok")[:200]}
        except AIError as e:
            return {"ok": False, "error": str(e)[:300]}

    # -- history (local files, no daemon round-trip) ---------------------------

    def history(self, q: str = "", limit: int = 200) -> list[dict]:
        return history_mod.search(q, limit)

    def history_delete(self, ts: float) -> int:
        return history_mod.delete(ts)

    def history_clear(self) -> int:
        return history_mod.clear()

    def history_update_text(self, ts: float, text: str) -> bool:
        """Inline repair: rewrite one entry's text (research §4)."""
        return history_mod.update_text(ts, text)

    def history_audio(self, ts: float) -> Any:
        return history_mod.audio_path_for(ts)

    def today_stats(self) -> dict:
        return history_mod.today_stats(history_mod.read_all())

    def export_zip(self, path) -> tuple[int, list[str]]:
        """(entries exported, notes about skipped/refused audio)."""
        notes: list[str] = []
        n = history_mod.export_zip(path, on_note=notes.append)
        return n, notes

    # -- dictionary auto-learning (direct reads; accept saves via set_config) --

    def dict_suggestions(self) -> list[dict]:
        """Pending custom-dictionary suggestions learned from history edits
        (suggest-only; see processing/dict_learn.py)."""
        from ..processing import dict_learn
        return dict_learn.pending_suggestions(load_config(),
                                              history_mod.read_all())

    def dict_suggestion_accept(self, heard: str, corrected: str) -> dict:
        """Merge the pair into processing.dictionary through the validated
        save path (daemon live-apply or file-only degraded mode)."""
        from ..processing import dict_learn
        dictionary = (load_config().get("processing", {})
                      .get("dictionary") or [])
        merged = dict_learn.accept_merge(dictionary, heard, corrected)
        resp = self.set_config({"processing": {"dictionary": merged}})
        ok = not resp.get("rejected") and not resp.get("errors")
        if ok:
            dict_learn.record_accepted(heard, corrected)
        return {"ok": ok, "dictionary": merged, "changed": resp.get("changed"),
                "rejected": resp.get("rejected"), "errors": resp.get("errors")}

    def dict_suggestion_dismiss(self, heard: str, corrected: str) -> None:
        """Record the pair as permanently dismissed."""
        from ..processing import dict_learn
        dict_learn.dismiss(heard, corrected)

    # -- prompt profiles (sidecar file beside the config; dict_learn
    #    precedent - direct access, no daemon round-trip) ------------------------

    def prompt_profiles(self) -> dict[str, str]:
        """Named presets of the AI base prompt ({} when unreadable)."""
        from ..ai import profiles
        try:
            return profiles.load_profiles()
        except Exception:
            return {}  # a broken sidecar must not take the window down

    def _profile_call(self, fn, *args) -> dict:
        from ..ai import profiles
        try:
            return {"ok": True, "error": None,
                    "profiles": fn(*args)}
        except ValueError as e:
            return {"ok": False, "error": str(e),
                    "profiles": profiles.load_profiles()}

    def prompt_profile_save(self, name: str, prompt: str) -> dict:
        from ..ai.profiles import save_named
        return self._profile_call(save_named, name, prompt)

    def prompt_profile_rename(self, old: str, new: str) -> dict:
        from ..ai.profiles import rename_profile
        return self._profile_call(rename_profile, old, new)

    def prompt_profile_delete(self, name: str) -> dict:
        from ..ai.profiles import delete_profile
        return self._profile_call(delete_profile, name)
