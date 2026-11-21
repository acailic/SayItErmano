"""GTK-free client tests: daemon-down degraded mode (file-only saves).

Moved out of test_gtkui.py so headless boxes run them without a display:
fluidvoice.gtkui.client has no GTK imports by design.
"""
from __future__ import annotations

from fluidvoice.gtkui.client import Client, ClientError


class TestClientFileOnlyMode:
    """Daemon-down degraded mode: file-only saves, file-based config reads."""

    @staticmethod
    def _dead_client() -> Client:
        from fluidvoice.gtkui.client import ClientError

        def boom(action, **kwargs):
            raise ClientError("daemon down")

        c = Client()
        c._request = boom
        return c

    def test_set_config_without_daemon_writes_file(self, tmp_path, monkeypatch):
        from fluidvoice import paths
        monkeypatch.setattr(paths, "config_file",
                            lambda: tmp_path / "c.toml")
        c = self._dead_client()
        resp = c.set_config({"sounds": {"volume": 0.4}})
        assert resp["ok"] and resp["changed"] == ["sounds.volume"]
        assert "file" in resp["note"]
        from fluidvoice.config import load_config
        assert load_config(tmp_path / "c.toml")["sounds"]["volume"] == 0.4

    def test_get_config_without_daemon(self):
        c = self._dead_client()
        cfg, from_daemon = c.get_config()
        assert from_daemon is False and cfg["hotkey"]["key"]


class TestClientUpdateCheck:
    """The manual update-check surface (Settings → About, #22): the
    client sends the `update-check` control action and passes the
    response through unchanged (the daemon owns the probe)."""

    def test_sends_update_check_action(self):
        c = Client()
        seen = {}

        def fake_request(action, **kwargs):
            seen["action"] = action
            seen["kwargs"] = kwargs
            return {"ok": True, "update_available": "0.6.0",
                    "update_url": "u",
                    "update": {"checked": True, "update_available": "0.6.0",
                               "error": None,
                               "route_guide": "https://example/guide"}}

        c._request = fake_request
        resp = c.update_check()
        assert seen["action"] == "update-check"
        assert seen["kwargs"] == {}
        assert resp["update_available"] == "0.6.0"
        assert resp["update"]["route_guide"] == "https://example/guide"

    def test_daemon_down_raises_client_error(self):
        c = Client()
        c._request = self._dead_request()
        try:
            c.update_check()
            raised = False
        except ClientError:
            raised = True
        assert raised

    @staticmethod
    def _dead_request():
        def boom(action, **kwargs):
            raise ClientError(f"daemon down ({action})")

        return boom


class TestClientDictionarySuggestions:
    """The three dict-learning client methods: direct reads for the list,
    accept merges through the validated save path, dismiss writes the
    decisions store."""

    @staticmethod
    def _patch_paths(tmp_path, monkeypatch):
        from fluidvoice import paths
        hpath = tmp_path / "history.jsonl"
        spath = tmp_path / "dictionary-suggestions.json"
        cpath = tmp_path / "c.toml"
        monkeypatch.setattr(paths, "history_file", lambda: hpath)
        monkeypatch.setattr(paths, "dictionary_suggestions_file",
                            lambda: spath)
        monkeypatch.setattr(paths, "config_file", lambda: cpath)
        return hpath, spath, cpath

    @staticmethod
    def _dead_client() -> Client:
        from fluidvoice.gtkui.client import ClientError

        def boom(action, **kwargs):
            raise ClientError("daemon down")

        c = Client()
        c._request = boom
        return c

    def test_suggestions_derive_from_history(self, tmp_path, monkeypatch):
        from fluidvoice import history
        hpath, _, _ = self._patch_paths(tmp_path, monkeypatch)
        history.append({"ts": 1.0, "text": "open the miro board app"})
        history.update_text(1.0, "open the Miro board app")
        history.append({"ts": 2.0, "text": "open the miro board now"})
        history.update_text(2.0, "open the Miro board now")
        c = self._dead_client()
        assert c.dict_suggestions() == [
            {"heard": "miro board", "corrected": "Miro board", "count": 2}]

    def test_accept_merges_saves_and_records(self, tmp_path, monkeypatch):
        from fluidvoice import history
        from fluidvoice.config import load_config
        hpath, spath, cpath = self._patch_paths(tmp_path, monkeypatch)
        history.append({"ts": 1.0, "text": "please send the flud report"})
        history.update_text(1.0, "please send the fluid report")
        history.append({"ts": 2.0, "text": "the flud report again"})
        history.update_text(2.0, "the fluid report again")
        c = self._dead_client()
        resp = c.dict_suggestion_accept("flud", "fluid")
        assert resp["ok"] is True
        assert resp["dictionary"] == [
            {"triggers": ["flud"], "replacement": "fluid"}]
        # saved through the validated (file-only) path
        assert load_config(cpath)["processing"]["dictionary"] == [
            {"triggers": ["flud"], "replacement": "fluid"}]
        # accepted pairs never resuggest
        assert c.dict_suggestions() == []
        import json
        store = json.loads(spath.read_text(encoding="utf-8"))
        assert store["accepted"] == [["flud", "fluid"]]

    def test_dismiss_writes_permanent_decision(self, tmp_path, monkeypatch):
        from fluidvoice import history
        hpath, spath, _ = self._patch_paths(tmp_path, monkeypatch)
        history.append({"ts": 1.0, "text": "check gnu plot output"})
        history.update_text(1.0, "check gnuplot output")
        history.append({"ts": 2.0, "text": "run gnu plot now"})
        history.update_text(2.0, "run gnuplot now")
        c = self._dead_client()
        assert c.dict_suggestions() == [
            {"heard": "gnu plot", "corrected": "gnuplot", "count": 2}]
        c.dict_suggestion_dismiss("gnu plot", "gnuplot")
        assert c.dict_suggestions() == []

