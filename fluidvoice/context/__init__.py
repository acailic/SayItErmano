"""Context seam factory: per-session provider selection with graceful
degradation.

The public surface is deliberately tiny:

- ``reader_for(cfg)`` -> a zero-arg callable or None. The pipeline calls
  it exactly once, immediately before insertion. There is no provider
  handle to hold, no cached context, no way to "pre-read" through this
  API.
- ``read_focus_context(cfg)`` -> FocusContext: a direct one-shot read
  for callers outside the pipeline (doctor-style diagnostics).

Provider choice (`context.provider`, default "auto"):

- "auto": wayland session -> AT-SPI (identity + focused-field text);
  x11/unknown -> the X11 identity provider.
- "x11" / "atspi": force one provider (atspi works on X11 too — early
  adopters get role/selection/preceding there).
- "none": disable context reads entirely.

A provider whose dependencies are missing (no xdotool; no pyatspi/gi
Atspi) reports unavailable and the factory yields None: the take then
runs with exactly today's behavior. Master switch: `context.enabled`
(default false — prototype-grade; the Wayland-parity smoke matrix
gates flipping the default).
"""
from __future__ import annotations

from .. import session as session_mod
from .atspi_provider import AtspiProvider
from .base import (
    MAX_PRECEDING_CHARS,
    ContextProvider,
    ContextReader,
    FocusContext,
    apply_continuation,  # noqa: F401 - re-export (consumer surface)
    ends_sentence,  # noqa: F401
    missing_context,
)
from .x11_provider import X11Provider

PROVIDER_NAMES = ("auto", "x11", "atspi", "none")


def _max_preceding(cfg: dict) -> int:
    raw = (cfg.get("context", {}) or {}).get("max_preceding_chars", 120)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 120
    return max(0, min(value, MAX_PRECEDING_CHARS))


def resolve_provider_name(cfg: dict, info=None) -> str:
    """The provider name for this session (pure; "none" when disabled)."""
    c = cfg.get("context", {}) or {}
    if not c.get("enabled"):
        return "none"
    pref = str(c.get("provider", "auto") or "auto").strip().lower()
    if pref not in PROVIDER_NAMES:
        pref = "auto"
    if pref == "auto":
        info = info or session_mod.current()
        pref = "atspi" if info.is_wayland else "x11"
    return pref


def make_provider(name: str) -> ContextProvider | None:
    """Construct one provider by name (import/dependency probing happens
    lazily inside the provider; None is never returned here — an
    unavailable provider is returned and reports available() False)."""
    if name == "x11":
        return X11Provider()
    if name == "atspi":
        return AtspiProvider()
    return None


def reader_for(cfg: dict, info=None,
               provider: ContextProvider | None = None) -> ContextReader | None:
    """The insertion-time reader for this config/session, or None when
    context reads are disabled or the provider is unavailable.

    `provider` (tests) replaces the constructed provider entirely."""
    name = resolve_provider_name(cfg, info)
    if name == "none":
        return None
    prov = provider or make_provider(name)
    if prov is None:
        return None
    try:
        if not prov.available():
            return None
    except Exception:  # noqa: BLE001 - an unavailable probe is None
        return None
    max_preceding = _max_preceding(cfg)

    def read() -> FocusContext:
        try:
            return prov.read_focus_context(max_preceding)
        except Exception:  # noqa: BLE001 - one bad read degrades quietly
            return missing_context(getattr(prov, "name", name))

    return read


def read_focus_context(cfg: dict, info=None,
                       provider: ContextProvider | None = None) -> FocusContext:
    """One-shot read (diagnostics). Same degradation rules as the
    pipeline path."""
    reader = reader_for(cfg, info=info, provider=provider)
    if reader is None:
        return missing_context("disabled")
    return reader()
