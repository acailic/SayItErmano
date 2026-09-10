"""The P2 context seam: focused-field context read at insertion time.

`FocusContext` is the single value crossing this seam: app identity,
accessible role, selection and a hard-bounded slice of the text
preceding the caret. Privacy invariants (proved by tests, enforced by
construction):

- the preceding/selection text is BOUNDED — the frozen dataclass itself
  truncates to ``MAX_PRECEDING_CHARS`` in ``__post_init__``, so even a
  buggy provider cannot smuggle more;
- the value is EPHEMERAL — it is produced by a provider's single
  ``read_focus_context()`` call, consumed in the pipeline's insert step,
  and never stored on the pipeline, the daemon, the history entry or a
  log line (its ``__repr__`` shows lengths, not content);
- it is read ONLY immediately before insertion. The factory
  (:mod:`fluidvoice.context`) hands the pipeline a one-shot callable,
  not a provider handle, so pre-reading has no API to ride on.

Consumers MUST degrade to exactly today's behavior whenever a context
is unusable (``missing``/``stale``) — that compat floor is pinned by
tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

#: Hard upper bound for any text field carried in a FocusContext. The
#: user-tunable size (`context.max_preceding_chars`) may be smaller but
#: can never exceed this.
MAX_PRECEDING_CHARS = 500

#: Characters that end a sentence for continuation capitalization.
#: Includes the CJK full-width forms whisper emits for zh/ja.
_SENTENCE_END = ".!?…。！？…"

#: Accessible role names treated as search-like (GAAV continuous
#: dictation target: lowercase, no trailing period).
_SEARCH_ROLE_MARKERS = ("search",)


@dataclass(frozen=True)
class FocusContext:
    """What the focused field looked like at insertion time.

    All content fields are optional: a provider fills what it can and
    leaves the rest ``None``. ``missing`` marks a failed/no read
    (consumers ignore the context entirely); ``stale`` marks an
    identity-only read where the focused object could not be verified
    (window-level fallback: identity consumers may still use it, field
    consumers must not).
    """

    app_id: str | None = None
    window_title: str | None = None
    accessible_role: str | None = None
    selection_text: str | None = None
    preceding_text: str | None = None
    provider_name: str = ""
    stale: bool = False
    missing: bool = False

    def __post_init__(self) -> None:
        # Bound enforcement lives HERE (not in providers): the invariant
        # holds no matter who constructs the value.
        for name in ("selection_text", "preceding_text"):
            value = getattr(self, name)
            if isinstance(value, str) and len(value) > MAX_PRECEDING_CHARS:
                object.__setattr__(self, name,
                                   value[-MAX_PRECEDING_CHARS:])

    def __repr__(self) -> str:  # privacy: never print field content
        def _len(v: str | None) -> int:
            return len(v) if v else 0
        return (f"FocusContext(provider={self.provider_name!r}, "
                f"app_id={'<set>' if self.app_id else None}, "
                f"title={'<set>' if self.window_title else None}, "
                f"role={self.accessible_role!r}, "
                f"selection=<{_len(self.selection_text)} chars>, "
                f"preceding=<{_len(self.preceding_text)} chars>, "
                f"stale={self.stale}, missing={self.missing})")

    @property
    def usable(self) -> bool:
        """True when the read succeeded. Identity consumers (profile
        matching) may run even on a stale window-level fallback read —
        the active window's app IS the focused app — so this only
        excludes a failed read."""
        return not self.missing

    @property
    def field_usable(self) -> bool:
        """True when the read is field-level (role/text consumers may
        run): not missing, not stale, and a verified focused object."""
        return not (self.missing or self.stale)

    @property
    def identity(self) -> str | None:
        """The best app-identity string for profile matching."""
        return self.app_id or self.window_title or None

    @property
    def search_like(self) -> bool:
        """The focused field looks like a search box (GAAV target)."""
        role = (self.accessible_role or "").lower()
        return any(m in role for m in _SEARCH_ROLE_MARKERS)


#: A factory-produced insertion-time reader: zero-arg, never raises,
#: returns a FocusContext (possibly marked missing). ``None`` return of
#: the FACTORY means "context disabled/unavailable for this session".
ContextReader = Callable[[], "FocusContext"]


@runtime_checkable
class ContextProvider(Protocol):
    """One backend behind the seam. ``read_focus_context`` is the only
    data read; ``available`` is the cheap pre-check used by the factory
    (tool/dependency probing, no live query)."""

    name: str

    def available(self) -> bool: ...

    def read_focus_context(self,
                           max_preceding: int = 120) -> FocusContext: ...


def missing_context(provider_name: str = "") -> FocusContext:
    """The canonical 'nothing was readable' value."""
    return FocusContext(provider_name=provider_name, missing=True)


# ---------------------------------------------------------------------------
# Continuation formatting (pure, hermetically tested)
# ---------------------------------------------------------------------------

def ends_sentence(preceding: str) -> bool:
    """True when `preceding` visibly ends a sentence (ignoring trailing
    whitespace — a paragraph break also starts a new sentence)."""
    tail = preceding.rstrip()
    return bool(tail) and tail[-1] in _SENTENCE_END


def apply_continuation(text: str, preceding: str | None, *,
                       capitalize: bool = True) -> str:
    """Join `text` onto the text already in the field.

    - spacing: one leading space iff `preceding` is non-empty and does
      not already end with whitespace (mid-line continuation);
    - capitalization: the first letter is upper-cased iff the field is
      empty/blank or `preceding` visibly ends a sentence; never
      lower-cased (mid-sentence takes keep polish casing — downcasing
      would mangle proper nouns);
    - `preceding` None (unknown — no accessibility data): the text is
      returned untouched, exactly today's behavior;
    - `capitalize=False` lets a GAAV consumer keep the first letter
      lowercase (search boxes).
    """
    if not text:
        return text
    if preceding is None:
        return text
    out = text
    if preceding and not preceding[-1].isspace():
        out = " " + out
    if capitalize and (not preceding.strip() or ends_sentence(preceding)):
        if out[:1].isalpha() and out[:1].islower():
            out = out[:1].upper() + out[1:]
    return out


@dataclass
class ReadLimits:
    """Bounded-work guarantees for provider tree walks (AT-SPI)."""

    apps: int = 16
    windows: int = 32
    children: int = 64
    depth: int = 24
    nodes: int = 400
    _visited: int = field(default=0, compare=False)

    def visit(self) -> bool:
        """Consume one node slot; False when the budget is exhausted."""
        self._visited += 1
        return self._visited <= self.nodes
