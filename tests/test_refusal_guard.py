"""AI refusal guardrail (D5): is_refusal detection matrix + pipeline/
rewrite wiring + config surface. Pure + stub-based (no network)."""
from __future__ import annotations

import copy
import math
import struct
import wave
from pathlib import Path

import pytest

from fluidvoice import daemon as dm
from fluidvoice.config import DEFAULTS, coerce_setting, mask_secrets
from fluidvoice.processing.refusal import is_refusal

# -- detector matrix ---------------------------------------------------------

REFUSALS = [
    # the verbatim GetVoibe string (research insight 9)
    "I'm sorry, I can't assist with that.",
    "I'm sorry, but I cannot assist with this request.",
    "Sorry, I can't help with that.",
    "I am sorry, but I am unable to comply with your request.",
    "As an AI language model, I cannot assist with that request.",
    "I cannot assist with that.",
    "I must decline this request.",
    "I'm unable to assist with this.",
    "Unfortunately, I can't help with that.",
    "I apologize, but I cannot comply with this request.",
    "That request goes against my guidelines.",
    # markdown dressing
    "**I'm sorry**, but I cannot assist with that request.",
    "> I'm sorry, I can't assist with that.",
    "- As an AI, I can't help with this.",
    "## I cannot fulfill this request",
]

LEGIT = [
    "I'm sorry I'm late",
    "Sorry, I can't make it today",           # no assist/help/comply verb
    "I can't make it to the meeting tomorrow",
    "I can't do that, sorry",
    "The meeting moved to Friday, unfortunately",
    "I'm sorry to hear that. " + ("the deployment went fine and the "
                                  "rollback plan worked. " * 12),
    "As an aside, I parked the car downstairs",
    "Please apologize to the team for me",
    "",                                        # never a refusal
    "Sure! Here is the polished text: the quick brown fox.",
]


@pytest.mark.parametrize("text", REFUSALS)
def test_refusals_detected(text):
    assert is_refusal(text) is True


@pytest.mark.parametrize("text", LEGIT)
def test_legit_text_passes(text):
    assert is_refusal(text) is False


def test_sorry_family_requires_verb_within_window():
    # sorry at the start, refuse-verb far past the 240-char window -> pass
    filler = ("the quarterly numbers are in and everything looks "
              "great so far overall. " * 3)
    assert is_refusal("I'm sorry to bother you. " + filler
                      + " I cannot assist with that later.") is False


# -- config surface ----------------------------------------------------------

def test_config_default_and_coercion():
    assert DEFAULTS["ai"]["refusal_guard"] is True
    ok, val = coerce_setting("ai", "refusal_guard", False)
    assert ok and val is False
    ok, _ = coerce_setting("ai", "refusal_guard", "yes")
    assert ok is False


def test_config_roundtrip_and_mask():
    cfg = copy.deepcopy(DEFAULTS)
    ok, val = coerce_setting("ai", "refusal_guard", False)
    assert ok
    cfg["ai"]["refusal_guard"] = val
    masked = mask_secrets(cfg)
    assert masked["ai"]["refusal_guard"] is False


# -- pipeline wiring ---------------------------------------------------------

def _wav(path: Path) -> Path:
    n = 16000
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        frames = bytearray()
        for i in range(n):
            frames += struct.pack("<h", int(12000 * math.sin(
                2 * math.pi * 440 * i / 16000)))
        wf.writeframes(bytes(frames))
    if path.stat().st_size < 200:
        with open(path, "ab") as fh:
            fh.write(b"\0" * 300)
    return path


class _QuietUI:
    def __init__(self):
        self.notes: list[tuple[str, str]] = []

    def __call__(self, title, body="", **_):
        self.notes.append((title, body))


class _Backend:
    name = "stub"
    surfaces_detected_language = False

    def transcribe(self, wav, language=None):
        return {"text": "raw dictation words", "language": None,
                "duration": None, "segments": []}


@pytest.fixture()
def cfg():
    c = copy.deepcopy(DEFAULTS)
    c["ai"]["enabled"] = True
    return c


def _pipeline(cfg, polisher):
    ui_notes = _QuietUI()
    pipe = dm.DictationPipeline(cfg, _Backend(), polisher=polisher,
                                inserter=lambda t, c: "typed",
                                history_writer=lambda e, w: None,
                                logger=lambda _m: None)
    pipe.notify = ui_notes
    pipe._ui_notes = ui_notes
    return pipe


def test_polish_refusal_falls_back_to_raw(cfg, tmp_path):
    pipe = _pipeline(cfg, lambda t: "I'm sorry, I can't assist with that.")
    text, ai_used = pipe._polish("raw dictation words")
    assert text == "raw dictation words" and ai_used is False
    assert any("refused" in b for _t, b in pipe._ui_notes.notes)


def test_polish_normal_output_unchanged(cfg):
    pipe = _pipeline(cfg, lambda t: "Polished words.")
    text, ai_used = pipe._polish("raw dictation words")
    assert text == "Polished words." and ai_used is True
    assert pipe._ui_notes.notes == []


def test_guard_opt_out_types_the_refusal(cfg):
    cfg["ai"]["refusal_guard"] = False
    pipe = _pipeline(cfg, lambda t: "I cannot assist with that request.")
    text, ai_used = pipe._polish("raw dictation words")
    # opted out: the model's reply is trusted verbatim (documented foot-gun)
    assert text == "I cannot assist with that request." and ai_used is True


def test_guard_applies_through_full_run(cfg, tmp_path):
    inserted = []
    pipe = dm.DictationPipeline(cfg, _Backend(),
                                polisher=lambda t: "As an AI language "
                                                   "model, I cannot assist.",
                                inserter=lambda t, c: inserted.append(t),
                                history_writer=lambda e, w: None,
                                logger=lambda _m: None)
    out = pipe.run(_wav(tmp_path / "take.wav"), None)
    assert out["text"] == "raw dictation words" and out["ai"] is False
    assert inserted == ["raw dictation words"]


def test_rewrite_refusal_nothing_typed(cfg, tmp_path):
    notes = _QuietUI()
    pipe = _pipeline(cfg, polisher=lambda t: "x")
    pipe.notify = notes
    pipe.rewriter = lambda i, c: "I'm sorry, but I cannot help with that."
    out = pipe._rewrite("make this formal", None, "raw words", 1.0,
                        tmp_path / "r.wav")
    assert out is None
    assert any("refused" in b for _t, b in notes.notes)


# -- doctor ------------------------------------------------------------------

def test_doctor_lines_mention_guard():
    from fluidvoice import doctor
    c_off = copy.deepcopy(DEFAULTS)
    lines = doctor._ai_polish_lines(c_off)
    assert any("ai: disabled" in ln for ln in lines)
    c_on = copy.deepcopy(DEFAULTS)
    c_on["ai"]["enabled"] = True
    lines = doctor._ai_polish_lines(c_on)
    assert any("refusal guard: on" in ln for ln in lines)
    c_on["ai"]["refusal_guard"] = False
    assert any("refusal guard: OFF" in ln
               for ln in doctor._ai_polish_lines(c_on))


# -- prompt-leak guard (upstream #910) ---------------------------------------

from fluidvoice.processing.refusal import is_prompt_leak  # noqa: E402

PROMPT = ("You are a voice-to-text dictation cleaner. Clean and format "
          "the raw transcript. Remove filler words and never answer "
          "questions about yourself or anything else.")


@pytest.mark.parametrize("reply", [
    # the full prompt pasted back (the #910 case)
    PROMPT,
    "Sure! " + PROMPT + " Here is the text.",
    # any full instruction sentence
    "the notes say: clean and format the raw transcript. remove filler "
    "words and never answer questions",
])
def test_prompt_leak_detected(reply):
    assert is_prompt_leak(reply, PROMPT) is True


@pytest.mark.parametrize("reply", [
    "the meeting is at three, please clean up the room before then",
    "never answer the phone after hours",
    "clean and format seven words here",   # fewer than 8 consecutive
    "",
])
def test_prompt_leak_negatives(reply):
    assert is_prompt_leak(reply, PROMPT) is False


def test_leak_needs_a_prompt():
    assert is_prompt_leak("anything", "") is False
    assert is_prompt_leak("", PROMPT) is False


def test_polish_leak_falls_back_to_raw(cfg):
    from fluidvoice.ai.prompts import DEFAULT_DICTATION_PROMPT_BODY

    def leaker(_t):
        return DEFAULT_DICTATION_PROMPT_BODY   # model echoes the prompt

    pipe = _pipeline(cfg, leaker)
    text, ai_used = pipe._polish("raw dictation words")
    assert text == "raw dictation words" and ai_used is False
    assert any("prompt" in b for _t, b in pipe._ui_notes.notes)


def test_polish_leak_disabled_with_guard(cfg):
    cfg["ai"]["refusal_guard"] = False
    from fluidvoice.ai.prompts import DEFAULT_DICTATION_PROMPT_BODY

    pipe = _pipeline(cfg, lambda t: DEFAULT_DICTATION_PROMPT_BODY)
    text, ai_used = pipe._polish("raw dictation words")
    assert ai_used is True   # opted out: trusted verbatim


# -- over-correction guard (Ma et al. 2024 failure mode) ---------------------

from fluidvoice.processing.refusal import is_overcorrection  # noqa: E402


@pytest.mark.parametrize("polished,raw,want", [
    ("please renovate the building tomorrow",
     "please remodel the house tomorrow", True),
    ("the committee decided everything today",
     "the board determined all today", True),
    ("the quick brown house jumps", "the quick brown home jumps", False),
    ("dinner at 5:30 pm", "dinner at five thirty pm", False),
    ("Hello world.", "hello world ", False),
    ("Meeting moved.", "um uh the meeting has been moved", False),
])
def test_overcorrection_matrix(polished, raw, want):
    assert is_overcorrection(polished, raw) is want


def test_polish_overcorrection_falls_back(cfg):
    pipe = _pipeline(cfg, lambda t: "the committee decided everything today")
    text, ai_used = pipe._polish("the board determined all today")
    assert text == "the board determined all today" and ai_used is False
    assert any("rewrote" in b for _t, b in pipe._ui_notes.notes)


def test_polish_light_editing_still_used(cfg):
    pipe = _pipeline(cfg, lambda t: "Dinner at 5:30 pm.")
    text, ai_used = pipe._polish("um dinner at five thirty pm")
    assert text == "Dinner at 5:30 pm." and ai_used is True
