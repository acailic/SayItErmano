"""Shared fixtures for the mirrored gtkui test folder (org plan 5.8:
new tests land under folders mirroring the package)."""

import sys
from pathlib import Path

# reuse the StubClient from the flat-era display tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
