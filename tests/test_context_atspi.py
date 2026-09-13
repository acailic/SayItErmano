"""AT-SPI provider read logic against duck-typed fake accessibility
trees (pyatspi-style AND GIR-style naming). No real AT-SPI/D-Bus
connection is ever made - live behavior is the manual smoke matrix."""
from __future__ import annotations

from types import SimpleNamespace

from fluidvoice.context import AtspiProvider
from fluidvoice.context.atspi_provider import read_focus, read_field_text
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


class TestBusyDesktop:
    """Ledger F-31/F-32 (night run 2026-09-13): busy desktops register
    26-28 a11y apps with the focused one at the END, and more than one
    window can carry ACTIVE while unfocused."""

    @staticmethod
    def _busy_tree(filler_apps=25):
        """`filler_apps` helper apps first, then gedit with the focus."""
        filler = [FakeNode(name=f"helper-{i}", role="application",
                           states=(), children=[])
                  for i in range(filler_apps)]
        field = FakeNode(name="text", role="text", states=(FOCUSED,),
                         text=FakeText("mid sentence", caret=12),
                         app_name="org.gnome.gedit")
        gedit_win = FakeNode(name="*Untitled — gedit", role="frame",
                             states=(ACTIVE,), children=[field],
                             app_name="org.gnome.gedit")
        gedit = FakeNode(name="org.gnome.gedit", role="application",
                         states=(), children=[gedit_win])
        return FakeNode(children=filler + [gedit]), gedit_win

    def test_default_limits_reach_late_registered_apps(self):
        # F-31: gedit sits at index 25 of 26; defaults must find it.
        desktop, _win = self._busy_tree()
        ctx = read_focus(fake_module(desktop), 120)
        assert not ctx.missing and not ctx.stale
        assert ctx.app_id == "org.gnome.gedit"
        assert ctx.accessible_role == "text"
        assert ctx.preceding_text == "mid sentence"

    def test_explicit_small_apps_limit_still_truncates(self):
        # caller-injected limits stay authoritative (bounded work)
        desktop, _win = self._busy_tree()
        ctx = read_focus(fake_module(desktop), 120,
                         ReadLimits(apps=5))
        assert ctx.missing

    def test_unfocused_active_window_does_not_steal_identity(self):
        # F-32: an Electron window FIRST in desktop order keeps ACTIVE
        # while gedit holds the focus - the read must attach to gedit.
        electron_win = FakeNode(
            name="Codex|ChatGPT", role="frame", states=(ACTIVE,),
            children=[FakeNode(name="pane", role="panel", states=())],
            app_name="electron")
        electron = FakeNode(name="electron", role="application",
                            states=(), children=[electron_win])
        desktop, _win = self._busy_tree(filler_apps=1)
        desktop.children.insert(0, electron)
        ctx = read_focus(fake_module(desktop), 120)
        assert not ctx.stale and not ctx.missing
        assert ctx.app_id == "org.gnome.gedit"
        assert ctx.preceding_text == "mid sentence"

    def test_big_unfocused_tree_does_not_starve_later_candidates(self):
        # the probe budget is split per candidate: a deep unfocused tree
        # (Electron) must not eat the whole node budget before the true
        # window is probed
        class Chain:
            def __init__(self, depth=0):
                self._depth = depth

            name = "electron"

            def getState(self):
                return FakeState(ACTIVE)

            def getApplication(self):
                return SimpleNamespace(name="electron")

            def getRoleName(self):
                return "frame"

            @property
            def childCount(self):
                return 1

            def getChildAtIndex(self, i):
                return Chain(self._depth + 1)

        electron = SimpleNamespace(
            name="electron", role="application", childCount=1,
            getChildAtIndex=lambda i: Chain(),
            getState=lambda: FakeState(),
            getApplication=lambda: None,
            getRoleName=lambda: "application")
        field = FakeNode(name="text", role="text", states=(FOCUSED,),
                         text=FakeText("typed here", caret=10),
                         app_name="gedit")
        gedit_win = FakeNode(name="gedit", role="frame",
                             states=(ACTIVE,), children=[field],
                             app_name="gedit")
        gedit = FakeNode(name="gedit", role="application", states=(),
                         children=[gedit_win])
        desktop = SimpleNamespace(
            name="desktop", childCount=2,
            getChildAtIndex=lambda i: [electron, gedit][i],
            getState=lambda: FakeState(),
            getApplication=lambda: None,
            getRoleName=lambda: "application")
        ctx = read_focus(fake_module(desktop), 120,
                         ReadLimits(nodes=100, depth=200))
        assert not ctx.stale and not ctx.missing
        assert ctx.app_id == "gedit"
        assert ctx.preceding_text == "typed here"

    def test_all_active_unfocused_keeps_first_as_stale_identity(self):
        # compat floor: nothing carries FOCUSED -> first ACTIVE window
        # is the stale identity-only fallback, as before
        win_a = FakeNode(name="A", role="frame", states=(ACTIVE,),
                         children=[FakeNode(name="p", role="panel",
                                            states=())], app_name="app-a")
        win_b = FakeNode(name="B", role="frame", states=(ACTIVE,),
                         children=[FakeNode(name="p", role="panel",
                                            states=())], app_name="app-b")
        desktop = FakeNode(children=[
            FakeNode(name="app-a", role="application", states=(),
                     children=[win_a]),
            FakeNode(name="app-b", role="application", states=(),
                     children=[win_b])])
        ctx = read_focus(fake_module(desktop), 120)
        assert ctx.stale and ctx.usable
        assert ctx.app_id == "app-a"
        assert ctx.preceding_text is None


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


class TestGirUnboundInterfaces:
    """Ledger F-33 (night run 2026-09-13): on GIR installs without
    python-atspi (the project venv), the bound ``get_text(start, end)``
    hits the deprecated zero-arg interface getter and raises - the
    working form is the unbound ``Atspi.Text.get_text(obj, s, e)``."""

    @staticmethod
    def _gir_module(content, caret, selection):
        class Text:
            """Unbound interface calls - the verified GIR form."""

            @staticmethod
            def get_text(obj, start, end):
                return obj.content[start:end]

            @staticmethod
            def get_character_count(obj):
                return len(obj.content)

            @staticmethod
            def get_caret_offset(obj):
                return obj.caret

            @staticmethod
            def get_selection(obj, index):
                return obj.selection

        class Node:
            def __init__(self, name=None, role=None, states=(),
                         children=(), app_name=None):
                self.content = content
                self.caret = caret
                self.selection = selection
                self._name, self._role = name, role
                self._states = GirState(*states)
                self._children = list(children)
                self._app_name = app_name

            def get_name(self):
                return self._name

            def get_role_name(self):
                return self._role

            def get_state_set(self):
                return self._states

            def get_application(self):
                return Node(name=self._app_name) if self._app_name else None

            def get_child_count(self):
                return len(self._children)

            def get_child_at_index(self, i):
                return self._children[i]

            # deprecated zero-arg interface getters: raise when called
            # with arguments, mirroring the real GIR Accessible surface
            def get_text(self):
                raise TypeError("deprecated getter takes no arguments")

            def get_character_count(self):
                raise TypeError("deprecated getter takes no arguments")

            def get_caret_offset(self):
                raise TypeError("deprecated getter takes no arguments")

            def get_selection(self):
                raise TypeError("deprecated getter takes no arguments")

        field = Node(name="text", role="text", states=(FOCUSED,),
                     app_name="org.gnome.gedit")
        window = Node(name="Untitled", role="frame", states=(ACTIVE,),
                      children=[field], app_name="org.gnome.gedit")
        desktop = Node(children=[Node(name="org.gnome.gedit",
                                      role="application", states=(),
                                      children=[window])])
        return SimpleNamespace(
            Text=Text,
            get_desktop=lambda i: desktop,
            StateType=SimpleNamespace(ACTIVE=ACTIVE, FOCUSED=FOCUSED))

    def test_unbound_fallback_reads_preceding_and_selection(self):
        module = self._gir_module("well I don't wish to see it", 27,
                                  (5, 9))
        ctx = read_focus(module, 120)
        assert ctx.app_id == "org.gnome.gedit"
        assert ctx.accessible_role == "text"
        assert ctx.preceding_text == "well I don't wish to see it"
        assert ctx.selection_text == "I do"

    def test_no_text_interface_class_still_degrades(self):
        # a GIR module without the Text class (old introspection data):
        # everything degrades to None, identity/role survive
        module = self._gir_module("abc", 3, (0, 0))
        module = SimpleNamespace(
            get_desktop=module.get_desktop,
            StateType=module.StateType)
        ctx = read_focus(module, 120)
        assert ctx.app_id == "org.gnome.gedit"
        assert ctx.accessible_role == "text"
        assert ctx.preceding_text is None
        assert ctx.selection_text is None


class TestReadFieldText:
    """The insertion-side probe (F-34/F-35 verification): the focused
    field's text tail ending at the caret, None when unreadable."""

    def test_returns_text_ending_at_caret(self):
        field = FakeNode(name="f", role="text", states=(FOCUSED,),
                         text=FakeText("0123456789", caret=7),
                         app_name="app")
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[field], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        assert read_field_text(fake_module(desktop)) == "0123456"

    def test_bounded_to_max_chars(self):
        field = FakeNode(name="f", role="text", states=(FOCUSED,),
                         text=FakeText("a" * 5000, caret=5000),
                         app_name="app")
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[field], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        out = read_field_text(fake_module(desktop), max_chars=100)
        assert out == "a" * 100

    def test_no_focus_is_none(self):
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[FakeNode(name="p", role="panel",
                                             states=())], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        # stale fallback has no focused node -> the probe must say None
        assert read_field_text(fake_module(desktop)) is None

    def test_broken_text_ops_are_none_not_crash(self):
        class Broken:
            def getCharacterCount(self):
                raise RuntimeError("defunct")

            def getCaretOffset(self):
                raise RuntimeError("defunct")

        field = FakeNode(name="f", role="text", states=(FOCUSED,),
                         text=Broken(), app_name="app")
        window = FakeNode(name="W", role="frame", states=(ACTIVE,),
                          children=[field], app_name="app")
        desktop = FakeNode(children=[FakeNode(name="a", role="application",
                                              states=(),
                                              children=[window])])
        assert read_field_text(fake_module(desktop)) is None
