"""Generated config-reference freshness — org plan 4.3.

The unit-tier mirror of `scripts/gen_config_reference.py` (also a gate
step): the "All settings" block in docs/guides/configuration.md must be
byte-identical to what the settings registry renders. A registry change
(default, description, new key) without regenerating the doc fails
here — config docs cannot drift from code.
"""

from pathlib import Path

import scripts.gen_config_reference as gen
from fluidvoice.config import REGISTRY

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "guides" / "configuration.md"


def test_reference_block_matches_registry() -> None:
    problems = gen.check(DOC)
    assert not problems, "stale config reference:\n" + "\n".join(problems)


def test_every_registry_key_appears_in_doc() -> None:
    # even if markers were hand-moved, every (section, key) must be present
    text = DOC.read_text(encoding="utf-8")
    missing = [f"[{s}] {k}" for (s, k) in REGISTRY
               if f"### `[{s}]`" not in text or f"| `{k}` |" not in text]
    assert not missing, ("keys absent from the generated reference: "
                         + ", ".join(missing))


def test_cell_collapses_and_escapes() -> None:
    assert gen._cell("a |  b\nc") == r"a \| b c"
    assert gen._cell("  multi\nline\n  wrapped  ") == "multi line wrapped"
