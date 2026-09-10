"""P2 context seam: FocusContext invariants, the X11 provider (fake
runner - never a real X connection) and the factory/degradation rules."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from fluidvoice import context
from fluidvoice.context import (
    MAX_PRECEDING_CHARS,
    AtspiProvider,
    FocusContext,
    X11Provider,
    apply_continuation,
    ends_sentence,
    missing_context,
)
from fluidvoice.context.base import ReadLimits
from fluidvoice.session import SessionInfo

# ---------------------------------------------------------------------------
# FocusContext: bounds + privacy
# ---------------------------------------------------------------------------

class TestFocusContext:
    def test_preceding_text_truncated_to_hard_bound(self):
        ctx = FocusContext(preceding_text="x" * (MAX_PRECEDING_CHARS + 40),
                           provider_name="t")
        assert len(ctx.preceding_text) == MAX_PRECEDING_CHARS
        # the TAIL is kept (it is what abuts the caret)
        assert ctx.preceding_text.endswith("x")
        assert ctx.preceding_text.startswith("x")

    def test_selection_text_truncated_to_hard_bound(self):
        ctx = FocusContext(selection_text="s" * 900, provider_name="t")
        assert len(ctx.selection_text) == MAX_PRECEDING_CHARS

    def test_repr_never_contains_field_content(self):
        ctx = FocusContext(app_id="secret-app", window_title="secret title",
                           accessible_role="text",
                           selection_text="SELECTED SECRET",
                           preceding_text="PRECEDING SECRET",
                           provider_name="fake")
        rendered = repr(ctx)
        assert "SECRET" not in rendered
        assert "secret" not in rendered
        assert "preceding=<16 chars>" in rendered
        assert "selection=<15 chars>" in rendered

    def test_usable_and_field_usable_matrix(self):
        ok = FocusContext(app_id="a", provider_name="p")
        assert ok.usable and ok.field_usable
        stale = FocusContext(app_id="a", provider_name="p", stale=True)
        assert stale.usable and not stale.field_usable
        missing = FocusContext(provider_name="p", missing=True)
        assert not missing.usable and not missing.field_usable

    def test_identity_prefers_app_id_then_title(self):
        assert FocusContext(app_id="app", window_title="t").identity == "app"
        assert FocusContext(window_title="t").identity == "t"
        assert FocusContext().identity is None

    def test_search_like_roles(self):
        assert FocusContext(accessible_role="search").search_like
        assert FocusContext(accessible_role="Search Entry").search_like
        assert not FocusContext(accessible_role="text").search_like
        assert not FocusContext(accessible_role=None).search_like

    def test_missing_context_helper(self):
        ctx = missing_context("x11")
        assert ctx.missing and ctx.provider_name == "x11"
        assert not ctx.usable

    def test_frozen(self):
        ctx = FocusContext(app_id="a")
        with pytest.raises(Exception):
            ctx.app_id = "b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Continuation math (pure)
# ---------------------------------------------------------------------------

class TestApplyContinuation:
    def test_unknown_preceding_changes_nothing(self):
        assert apply_continuation("hello there", None) == "hello there"

    @pytest.mark.parametrize("preceding,expected", [
        ("", "Hello there"),                       # empty field: sentence start
        ("   ", "Hello there"),                    # blank: sentence start
        ("Done. ", "Hello there"),                 # terminator + space
        ("Done.\n", "Hello there"),                # paragraph break
        ("終わり。 ", "Hello there"),               # CJK terminator
        ("mid sentence", " hello there"),          # continuation: space, no cap
        ("ends with comma,", " hello there"),
        ("space already ", "hello there"),         # space present: no double
    ])
    def test_spacing_and_capitalization(self, preceding, expected):
        assert apply_continuation("hello there", preceding) == expected

    def test_capitalize_false_keeps_lowercase(self):
        assert apply_continuation("hello.", "", capitalize=False) == "hello."

    def test_non_alpha_first_char_not_capitalized(self):
        assert apply_continuation("/fix the deploy", "") == "/fix the deploy"

    def test_uppercase_first_char_untouched(self):
        assert apply_continuation("Hello", "") == "Hello"

    def test_empty_text_unchanged(self):
        assert apply_continuation("", "mid") == ""

    def test_ends_sentence(self):
        assert ends_sentence("Done.")
        assert ends_sentence("Done. \n")
        assert ends_sentence("really?!")
        assert not ends_sentence("mid sentence")
        assert not ends_sentence("")


# ---------------------------------------------------------------------------
# X11 provider (injectable runner: never a real subprocess in tests)
# ---------------------------------------------------------------------------

def _ok(stdout: bytes = b""):
    return subprocess.CompletedProcess([], 0, stdout, b"")


class TestX11Provider:
    def _provider(self, calls, responses):
        def runner(args, timeout):
            calls.append(list(args))
            return responses.get(tuple(args), _ok(b""))

        return X11Provider(runner=runner, which=lambda n: "/usr/bin/" + n)

    def test_reads_wm_class_and_title(self):
        calls: list = []

        def runner(args, timeout):
            calls.append(list(args))
            if args[:2] == ["xdotool", "getactivewindow"]:
                return _ok(b"12345\n")
            if args[:2] == ["xprop", "-id"]:
                return _ok(b'WM_CLASS(STRING) = "gnome-terminal-server", '
                           b'"gnome-terminal-server"\n')
            if args[:2] == ["xdotool", "getwindowname"]:
                return _ok("Terminal \u2014 bash\n".encode())
            return _ok()

        prov = X11Provider(runner=runner, which=lambda n: "/bin/" + n)
        ctx = prov.read_focus_context()
        assert ctx.app_id == "gnome-terminal-server"
        assert ctx.window_title == "Terminal \u2014 bash"
        assert ctx.usable and not ctx.missing and not ctx.stale
        # X11 has no accessibility text surface: field data stays None
        assert ctx.accessible_role is None
        assert ctx.selection_text is None
        assert ctx.preceding_text is None

    def test_missing_when_no_active_window(self):
        prov = X11Provider(runner=lambda a, t: _ok(b"\n"),
                           which=lambda n: "/bin/" + n)
        assert prov.read_focus_context().missing

    def test_missing_when_runner_raises(self):
        def boom(args, timeout):
            raise RuntimeError("x died")

        prov = X11Provider(runner=boom, which=lambda n: "/bin/" + n)
        assert prov.read_focus_context().missing

    def test_unavailable_without_xdotool(self):
        prov = X11Provider(which=lambda n: None)
        assert prov.available() is False
        assert prov.read_focus_context().missing

    def test_unavailable_on_wayland_session(self, monkeypatch):
        from fluidvoice import session as session_mod
        monkeypatch.setattr(session_mod, "current",
                            lambda: SessionInfo(type="wayland", desktop=""))
        prov = X11Provider(which=lambda n: "/bin/" + n)
        assert prov.available() is False


# ---------------------------------------------------------------------------
# Factory + degradation
# ---------------------------------------------------------------------------

class FakeProvider:
    name = "fake"

    def __init__(self, ctx=None, available=True, error=None):
        self.ctx = ctx or missing_context("fake")
        self._available = available
        self.error = error
        self.reads: list[int] = []

    def available(self) -> bool:
        return self._available

    def read_focus_context(self, max_preceding: int = 120) -> FocusContext:
        self.reads.append(max_preceding)
        if self.error:
            raise self.error
        return self.ctx


def _cfg(**over):
    cfg = {"context": {"enabled": True, "provider": "auto",
                       "max_preceding_chars": 120}}
    cfg["context"].update(over)
    return cfg


class TestFactory:
    def test_disabled_config_yields_none(self):
        assert context.reader_for(_cfg(enabled=False)) is None

    def test_provider_none_yields_none(self):
        assert context.reader_for(_cfg(provider="none")) is None

    def test_resolve_provider_name_per_session(self):
        wl = SessionInfo(type="wayland", desktop="")
        x11 = SessionInfo(type="x11", desktop="")
        assert context.resolve_provider_name(_cfg(), info=wl) == "atspi"
        assert context.resolve_provider_name(_cfg(), info=x11) == "x11"
        assert context.resolve_provider_name(_cfg(provider="atspi"),
                                             info=x11) == "atspi"
        assert context.resolve_provider_name(_cfg(enabled=False)) == "none"
        assert context.resolve_provider_name(_cfg(provider="bogus"),
                                             info=wl) == "atspi"

    def test_unavailable_provider_yields_none(self):
        assert context.reader_for(_cfg(provider="x11"),
                                  provider=FakeProvider(available=False)) \
            is None

    def test_reader_returns_context_and_clamps_bound(self):
        fake = FakeProvider(FocusContext(app_id="a", provider_name="fake"))
        reader = context.reader_for(_cfg(max_preceding_chars=7),
                                    provider=fake)
        assert reader is not None
        assert reader().app_id == "a"
        assert fake.reads == [7]

    def test_bound_clamped_to_hard_cap(self):
        fake = FakeProvider(FocusContext(provider_name="fake"))
        reader = context.reader_for(_cfg(max_preceding_chars=10_000),
                                    provider=fake)
        reader()
        assert fake.reads == [MAX_PRECEDING_CHARS]

    def test_reader_swallows_provider_exceptions(self):
        fake = FakeProvider(error=RuntimeError("atspi bus died"))
        reader = context.reader_for(_cfg(), provider=fake)
        ctx = reader()
        assert ctx.missing and not ctx.usable

    def test_read_focus_context_one_shot_disabled(self):
        assert context.read_focus_context(_cfg(enabled=False)).missing

    def test_read_focus_context_one_shot(self):
        fake = FakeProvider(FocusContext(app_id="zed", provider_name="fake"))
        assert context.read_focus_context(_cfg(), provider=fake).app_id \
            == "zed"


# ---------------------------------------------------------------------------
# AT-SPI provider availability (import probing only - never a live bus)
# ---------------------------------------------------------------------------

class TestAtspiAvailability:
    def test_unavailable_import_degrades_to_missing(self, monkeypatch):
        provider = AtspiProvider()
        monkeypatch.setattr(provider, "_resolve",
                            lambda: (None, "no pyatspi, no gi"))
        assert provider.available() is False
        ctx = provider.read_focus_context()
        assert ctx.missing and ctx.provider_name == "atspi"

    def test_read_failure_degrades_to_missing(self):
        fake = SimpleNamespace(Registry=SimpleNamespace(
            getDesktop=lambda i: (_ for _ in ()).throw(RuntimeError("bus"))))
        provider = AtspiProvider(atspi_module=fake)
        assert provider.available() is True
        assert provider.read_focus_context().missing


# ---------------------------------------------------------------------------
# ReadLimits budget
# ---------------------------------------------------------------------------

def test_read_limits_budget():
    limits = ReadLimits(nodes=3)
    assert limits.visit() and limits.visit() and limits.visit()
    assert not limits.visit()  # exhausted
