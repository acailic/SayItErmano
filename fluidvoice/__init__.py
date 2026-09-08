"""SayItErmano - FluidVoice behavior ported to Linux.

A community port of altic-dev/FluidVoice (macOS) behavior to Linux:
global-hotkey dictation -> local speech-to-text -> optional AI polish ->
text inserted into the focused app. GPLv3.

The Python package keeps the upstream `fluidvoice` module name on purpose:
this is a port of FluidVoice, and internals credit it. Everything
user-facing - repo, deb package, command, launcher, dirs and env-var
overrides (SAYITERMANO_CONFIG, ...) - is SayItErmano.
"""

__version__ = "0.8.0"
