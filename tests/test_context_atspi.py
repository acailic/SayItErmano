"""AT-SPI provider read logic against duck-typed fake accessibility
trees (pyatspi-style AND GIR-style naming). No real AT-SPI/D-Bus
connection is ever made - live behavior is the manual smoke matrix."""
from __future__ import annotations

from types import SimpleNamespace

from fluidvoice.context import AtspiProvider
from fluidvoice.context.atspi_provider import read_focus
from fluidvoice.context.base import ReadLimits

ACTIVE, FOCUSED = 1, 2


class FakeState:
    def __init__(self, *flags):
        self.flags = set(flags)

    def contains(self, const):
        return const in self.flags


class FakeText:
    """pyatspi-style text interface handle."""

    def __init__(self, content, caret, selection=None):
        self.content = content
        self.caret = caret
        self.selection = selection or (0, 0)

    def getCharacterCount(self):
        return len(self.content)

    def getCaretOffset(self):
        return self.caret

    def getText(self, start, end):
        return self.content[start:end]

    def getSelection(self, index):
        return self.selection


class FakeNode:
    def __init__(self, name=None, role=None, states=(), children=(),
                 text=None, app_name=None):
        self.name = name
        self.role = role
        self.state = FakeState(*states)
        self.children = list(children)
        self.text = text
        self._app_name = app_name

    def getState(self):
        return self.state

    def getRoleName(self):
        if self.role is None:
            raise NotImplementedError
        return self.role

    def getApplication(self):
        if self._app_name is None:
            return None
        return SimpleNamespace(name=self._app_name)

    @property
    def childCount(self):
        return len(self.children)

    def getChildAtIndex(self, i):
        return self.children[i]

    def queryText(self):
        if self.text is None:
            raise NotImplementedError
        return self.text


def fake_module(desktop):
    return SimpleNamespace(
        Registry=SimpleNamespace(getDesktop=lambda i: desktop),
        STATE_ACTIVE=ACTIVE, STATE_FOCUSED=FOCUSED)


def terminal_tree():
    """desktop -> gnome-terminal app -> ACTIVE window -> focused text obj."""
    field = FakeNode(
        name="terminal text", role="text", states=(FOCUSED,),
        text=FakeText("echo hello world", caret=16,
                      selection=(5, 10)),
        app_name="gnome-terminal-server")
    window = FakeNode(name="Terminal", role="frame", states=(ACTIVE,),
                      children=[FakeNode(name="vbox", role="panel",
                                         states=(),
                                         children=[field])],
                      app_name="gnome-terminal-server")
    desktop = FakeNode(children=[FakeNode(name="app", role="application",
                                          states=(),
                                          children=[window])])
    return desktop, window, field


class TestReadFocus:
    def test_full_field_read(self):
        desktop, _w, _f = terminal_tree()
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.provider_name == "atspi"
        assert not ctx.missing and not ctx.stale
        assert ctx.app_id == "gnome-terminal-server"
        assert ctx.window_title == "Terminal"
        assert ctx.accessible_role == "text"
        assert ctx.selection_text == "hello"
        assert ctx.preceding_text == "echo hello world"

    def test_preceding_bounded_to_request(self):
        desktop, _w, _f = terminal_tree()
        # app -> window -> vbox -> field
        field = desktop.children[0].children[0].children[0].children[0]
        field.text = FakeText("a" * 1000, caret=1000)
        ctx = read_focus(fake_module(desktop), 120)
        assert len(ctx.preceding_text) == 120
        assert ctx.preceding_text == "a" * 120

    def test_no_active_window_is_missing(self):
        desktop = FakeNode(children=[
            FakeNode(name="app", role="application", states=(),
                     children=[FakeNode(name="win", role="frame",
                                        states=())])])
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.missing

    def test_no_desktop_is_missing(self):
        broken = SimpleNamespace(Registry=SimpleNamespace(getDesktop=None))
        assert read_focus(broken, 120).missing

    def test_window_fallback_is_stale_identity_only(self):
        # active window, but NOTHING below reports FOCUSED
        window = FakeNode(name="Weird App", role="frame", states=(ACTIVE,),
                          children=[FakeNode(name="pane", role="panel",
                                             states=())],
                          app_name="weird-app")
        desktop = FakeNode(children=[FakeNode(name="app", role="application",
                                              states=(),
                                              children=[window])])
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.usable              # identity consumers may run
        assert not ctx.field_usable    # field consumers must not
        assert ctx.stale
        assert ctx.app_id == "weird-app"
        assert ctx.window_title == "Weird App"
        assert ctx.preceding_text is None and ctx.selection_text is None
        assert ctx.accessible_role is None

    def test_field_without_text_interface(self):
        field = FakeNode(name="button", role="push button",
                         states=(FOCUSED,), app_name="some-app")
        window = FakeNode(name="Win", role="frame", states=(ACTIVE,),
                          children=[field], app_name="some-app")
        desktop = FakeNode(children=[FakeNode(name="app", role="application",
                                              states=(),
                                              children=[window])])
        ctx = read_focus(fake_module(desktop), 120)
        assert not ctx.stale and not ctx.missing
        assert ctx.accessible_role == "push button"
        assert ctx.preceding_text is None and ctx.selection_text is None

    def test_caret_at_start_yields_empty_preceding(self):
        field = FakeNode(name="f", role="text", states=(FOCUSED,),
                         text=FakeText("hello", caret=0))
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[field], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.preceding_text == ""   # sentence start, not unknown
        assert ctx.selection_text is None  # empty selection -> None

    def test_broken_text_ops_degrade_not_crash(self):
        class ExplodingText:
            def getCharacterCount(self):
                raise RuntimeError("defunct")

            def getCaretOffset(self):
                raise RuntimeError("defunct")

            def getText(self, s, e):
                raise RuntimeError("defunct")

            def getSelection(self, i):
                raise RuntimeError("defunct")

        field = FakeNode(name="f", role="text", states=(FOCUSED,),
                         text=ExplodingText(), app_name="app")
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[field], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.app_id == "app" and ctx.accessible_role == "text"
        assert ctx.preceding_text is None and ctx.selection_text is None

    def test_node_budget_stops_unbounded_walks(self):
        # an ever-growing tree (every node spawns a fresh child): the
        # node budget must terminate the walk, not hang it
        class Cyc:
            name = "W"

            def getState(self):
                return FakeState(ACTIVE)

            def getApplication(self):
                return SimpleNamespace(name="app")

            def getRoleName(self):
                return "frame"

            @property
            def childCount(self):
                return 1

            def getChildAtIndex(self, i):
                return Cyc()

        desktop = SimpleNamespace(
            name="desktop", childCount=1,
            getChildAtIndex=lambda i: Cyc(),
            getState=lambda: FakeState(),
            getApplication=lambda: None,
            getRoleName=lambda: "application")
        ctx = read_focus(fake_module(desktop), 120,
                         ReadLimits(nodes=25, depth=100))
        # terminated without hanging; no FOCUSED anywhere -> stale
        # identity-only fallback
        assert ctx.stale and ctx.app_id == "app"


class TestProviderShell:
    def test_injected_module_used(self):
        desktop, _w, _f = terminal_tree()
        provider = AtspiProvider(atspi_module=fake_module(desktop))
        assert provider.available() is True
        ctx = provider.read_focus_context(120)
        assert ctx.app_id == "gnome-terminal-server"

    def test_read_exception_degrades_to_missing(self):
        class Boom:
            pass

        provider = AtspiProvider(atspi_module=Boom())
        assert provider.read_focus_context().missing


# ---------------------------------------------------------------------------
# GIR-style naming (snake_case) duck adapters
# ---------------------------------------------------------------------------

class GirState:
    def __init__(self, *flags):
        self.flags = set(flags)

    def contains(self, const):
        return const in self.flags


class GirNode:
    def __init__(self, name=None, role=None, states=(), children=(),
                 content=None, caret=None, app_name=None):
        self._name = name
        self._role = role
        self._states = GirState(*states)
        self._children = list(children)
        self._content = content
        self._caret = caret
        self._app_name = app_name

    def get_name(self):
        return self._name

    def get_role_name(self):
        return self._role

    def get_state_set(self):
        return self._states

    def get_application(self):
        return GirNode(name=self._app_name) if self._app_name else None

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, i):
        return self._children[i]

    # flattened text-interface methods (GIR exposes them on the object)
    def get_character_count(self):
        return len(self._content) if self._content is not None else 0

    def get_caret_offset(self):
        return self._caret or 0

    def get_text(self, start, end):
        return self._content[start:end] if self._content else ""

    def get_selection(self, index):
        return (0, 0)


class TestGirStyle:
    def test_snake_case_api_works(self):
        field = GirNode(name="entry", role="search", states=(FOCUSED,),
                        content="find files", caret=10, app_name="nautilus")
        window = GirNode(name="Files", role="frame", states=(ACTIVE,),
                         children=[field], app_name="nautilus")
        module = SimpleNamespace(
            get_desktop=lambda i: GirNode(
                children=[GirNode(name="app", role="application",
                                  states=(),
                                  children=[window])]),
            StateType=SimpleNamespace(ACTIVE=ACTIVE, FOCUSED=FOCUSED))
        ctx = read_focus(module, 120)
        assert ctx.app_id == "nautilus"
        assert ctx.accessible_role == "search"
        assert ctx.preceding_text == "find files"
        assert ctx.search_like
