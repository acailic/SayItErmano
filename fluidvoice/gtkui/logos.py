"""Provider logos for the AI polish endpoint (from the macOS app's assets).

`provider_for(base_url)` maps an OpenAI-compatible base URL to a logo key;
`logo_path()` resolves the key to a bundled PNG, preferring the white
"-light" recolor when the UI is dark (several logos are black-on-
transparent and would vanish on a dark panel).
"""
from __future__ import annotations

from importlib import resources

# (logo key, substrings matched against the lowercased base URL)
PROVIDERS: list[tuple[str, tuple[str, ...]]] = [
    ("glm", ("z.ai", "bigmodel")),
    ("openai", ("api.openai.com",)),
    ("groq", ("api.groq.com",)),
    ("anthropic", ("api.anthropic.com",)),
    ("cohere", ("cohere",)),
    ("cerebras", ("cerebras",)),
    ("nvidia", ("nvidia",)),
    ("openrouter", ("openrouter",)),
    ("xai", ("api.x.ai", "xai")),
    ("gemini", ("generativelanguage.googleapis.com", "gemini")),
    ("lmstudio", ("lmstudio", "lm.studio", ":1234")),
    ("ollama", ("ollama", ":11434")),
]


def provider_for(base_url: str) -> str | None:
    """Logo key for a base URL (None for unknown/local endpoints)."""
    url = (base_url or "").strip().lower()
    if not url:
        return None
    for key, needles in PROVIDERS:
        if any(n in url for n in needles):
            return key
    return None


def logo_path(provider: str | None, dark: bool = False) -> str | None:
    """Path to the bundled logo PNG, or None when there is nothing to show.

    `dark` picks the white recolor for monochrome marks on dark themes.
    """
    if not provider:
        return None
    names = [f"{provider}-light.png", f"{provider}.png"] if dark \
        else [f"{provider}.png"]
    try:
        base = resources.files("fluidvoice.assets").joinpath("providers")
        for name in names:
            ref = base.joinpath(name)
            if ref.is_file():
                with resources.as_file(ref) as p:
                    return str(p)
    except Exception:
        return None
    return None
