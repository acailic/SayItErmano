"""P1.3 — one configuration registry: every derived concern is proven by
parametrized tests over REGISTRY itself.

For each registered SettingSpec we prove, from the ONE spec:
- default: it lands in DEFAULTS, config.default() returns it, and a
  config file loading nothing else yields exactly the defaults;
- validation: coerce_setting dispatches to the spec's coercer, the
  registered default validates, and garbage rejects (for settable keys);
- persistence: "save" membership matches save_config's whitelist AND
  save_keys(), "settable" membership matches ALLOWED_SETTINGS, and keys
  without the mode are never applied by apply_settings;
- apply_mode: it alone decides the restart set and the engine-reload set;
- secret: it alone decides masking;
- template: the commented template's entry (docs + value) is rendered
  from the spec, and the whole template round-trips as valid TOML whose
  parsed content equals DEFAULTS.
"""
from __future__ import annotations

import copy
import tomllib

import pytest

from fluidvoice import config
from fluidvoice.config import REGISTRY, SettingSpec, template_text

# deterministic parametrization: registry order (section, key)
ALL = list(REGISTRY.items())
IDS = [f"{s}.{k}" for (s, k), _spec in ALL]


@pytest.fixture()
def cfg():
    return copy.deepcopy(config.DEFAULTS)


# -- the registry itself ------------------------------------------------------

def test_registry_covers_every_defaults_key():
    """DEFAULTS is a pure projection of the registry (no orphan keys,
    no registry entries missing from DEFAULTS)."""
    from_registry = set(REGISTRY)
    from_defaults = {(s, k) for s, keys in config.DEFAULTS.items()
                     for k in keys}
    assert from_registry == from_defaults


def test_registry_order_matches_defaults_order():
    assert [s for s in config.DEFAULTS] == list(dict.fromkeys(
        s for (s, _k) in REGISTRY))
    for section, keys in config.DEFAULTS.items():
        assert list(keys) == [k for (s, k) in REGISTRY if s == section]


@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_spec_fields_are_well_formed(pair, spec: SettingSpec):
    assert isinstance(spec, SettingSpec)
    assert (spec.section, spec.key) == pair
    assert spec.apply_mode in ("immediate", "engine", "restart")
    assert spec.persistence <= {"save", "settable"}
    assert isinstance(spec.secret, bool)
    assert callable(spec.coercer)


# -- concern 1: defaults -------------------------------------------------------

@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_default_comes_from_the_spec(pair, spec):
    section, key = pair
    assert config.DEFAULTS[section][key] == spec.default
    assert config.default(section, key) == spec.default


def test_load_config_yields_the_registry_defaults(tmp_path):
    """Public dict shape is the bar (P1.3): loading no file returns a
    deep copy of the registry defaults - same sections, same nesting."""
    empty = tmp_path / "none.toml"
    loaded = config.load_config(empty)
    assert loaded == copy.deepcopy(config.DEFAULTS)
    # deep: mutating the result never corrupts the registry
    loaded["general"]["terminal_apps"].append(" mutated")
    assert " mutated" not in config.default("general", "terminal_apps")


# -- concern 2: validation -----------------------------------------------------

# Optional-string keys whose EMPTY default is intentionally NOT accepted
# by the validated socket path (pre-registry behavior kept verbatim: the
# generic str rule rejects "" — clearing these means deleting the line
# from the file; the GTK collect path never sends empty for them).
_EMPTY_DEFAULT_NOT_SOCKET_VALID = {
    ("hotkey", "rewrite_key"), ("hotkey", "command_key"),
    ("hotkey", "paste_key"), ("hotkey", "language_key"),
    ("hotkey", "wayland_evdev_device"), ("model", "whispercpp_model"),
    ("ai", "model"), ("command", "working_dir"),
}


@pytest.mark.parametrize("pair, spec", [p for p in ALL
                                         if "settable" in p[1].persistence],
                         ids=[i for i, p in zip(IDS, ALL)
                              if "settable" in p[1].persistence])
def test_every_registered_default_validates(pair, spec):
    """A spec whose own default fails its own validation is a registry
    bug (the default could never be saved through the validated path) —
    except the explicit empty-string exception set above."""
    if pair in _EMPTY_DEFAULT_NOT_SOCKET_VALID:
        assert spec.default == ""  # guard the exception set itself
        assert config.coerce_setting(*pair, "")[0] is False
        return
    section, key = pair
    ok, _coerced = config.coerce_setting(section, key, spec.default)
    assert ok is True, f"{section}.{key} default {spec.default!r} rejected"


@pytest.mark.parametrize("pair, spec", [p for p in ALL
                                         if "settable" in p[1].persistence],
                         ids=[i for i, p in zip(IDS, ALL)
                              if "settable" in p[1].persistence])
def test_validation_dispatches_to_the_spec_coercer(pair, spec):
    """coerce_setting/validate are pure dispatch over REGISTRY - the
    spec's coercer IS the validation, for good values and garbage."""
    section, key = pair
    for value in (spec.default, None, "garbage-not-a-value", 0, -1, [], {}):
        assert config.coerce_setting(section, key, value) \
            == spec.coercer(value), (section, key, value)
        assert config.validate(section, key, value) is spec.coercer(value)[0]


@pytest.mark.parametrize("pair, spec", [p for p in ALL
                                         if "settable" not in p[1].persistence],
                         ids=[i for i, p in zip(IDS, ALL)
                              if "settable" not in p[1].persistence])
def test_non_settable_keys_never_apply(pair, spec):
    """Keys without the 'settable' persistence mode are ignored by
    apply_settings - never applied, never reported rejected."""
    section, key = pair
    local = copy.deepcopy(config.DEFAULTS)
    changed, rejected = config.apply_settings(
        local, {section: {key: "x" * 9}})
    assert changed == [] and rejected == []
    assert local[section][key] == spec.default
    assert key not in config.ALLOWED_SETTINGS.get(section, set())


# -- concern 3: persistence (save keys / allowed keys) -------------------------

@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_save_whitelist_comes_from_the_spec(pair, spec):
    section, key = pair
    in_whitelist = key in config._SAVE_WHITELIST.get(section, [])
    assert in_whitelist == ("save" in spec.persistence)
    assert in_whitelist == (key in config.save_keys().get(section, []))


@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_allowed_settings_comes_from_the_spec(pair, spec):
    section, key = pair
    assert (key in config.ALLOWED_SETTINGS.get(section, set())) \
        == ("settable" in spec.persistence)


def test_save_keys_matches_save_whitelist_exactly():
    """The derived whitelist IS save_keys() - order included (save_config
    iterates it)."""
    assert config.save_keys() == config._SAVE_WHITELIST


def test_documented_persistence_exceptions():
    """ai.api_key is env/file-only (never socket-settable);
    recording.sample_rate is a fixed constant neither path manages."""
    assert config.REGISTRY[("ai", "api_key")].persistence \
        == frozenset({"save"})
    assert config.REGISTRY[("recording", "sample_rate")].persistence \
        == frozenset()


def test_save_keys_survive_roundtrip(tmp_path, monkeypatch):
    from fluidvoice import paths as p
    monkeypatch.setattr(p, "config_file", lambda: tmp_path / "c.toml")
    local = copy.deepcopy(config.DEFAULTS)
    local["sounds"]["volume"] = 0.25
    config.save_config(local)
    # every save-persisted key with a non-empty default is physically
    # written (empty-default keys follow the documented carry-over rule:
    # absent = keep any previously saved value)
    written = tomllib.loads((tmp_path / "c.toml").read_text())
    for section, keys in config.save_keys().items():
        for key in keys:
            if config.default(section, key) not in ("", None, [], {}):
                assert key in written.get(section, {}), f"{section}.{key}"
    assert written["sounds"]["volume"] == 0.25


# -- concern 4: apply mode (restart / engine reload) ---------------------------

def test_restart_keys_come_from_apply_mode():
    assert config.restart_keys() \
        == {f"{s}.{k}" for (s, k), spec in REGISTRY.items()
            if spec.apply_mode == "restart"}
    assert config.restart_keys() == config.RESTART_REQUIRED


def test_reload_keys_come_from_apply_mode():
    assert config.reload_keys() \
        == {f"{s}.{k}" for (s, k), spec in REGISTRY.items()
            if spec.apply_mode == "engine"}
    assert config.reload_keys() == config.ENGINE_KEYS


@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_apply_mode_membership(pair, spec):
    dotted = f"{spec.section}.{spec.key}"
    in_restart = dotted in config.RESTART_REQUIRED
    in_engine = dotted in config.ENGINE_KEYS
    assert in_restart == (spec.apply_mode == "restart")
    assert in_engine == (spec.apply_mode == "engine")
    assert not (in_restart and in_engine)


def test_pinned_apply_mode_sets():
    """Behavior pins from the pre-registry era (tests + daemon rely on
    these exact sets)."""
    assert config.RESTART_REQUIRED == {"model.eager_warmup"}
    assert config.ENGINE_KEYS == {
        "model.backend", "model.name", "model.device", "model.compute",
        "model.whispercpp_model", "model.hotwords", "model.remote_url",
        "model.remote_model", "model.remote_api_key", "model.remote_timeout_s"}


# -- concern 5: secret masking --------------------------------------------------

@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_masking_comes_from_the_spec(pair, spec):
    section, key = pair
    assert config.is_secret(section, key) is spec.secret
    assert ((section, key) in config.SECRET_KEYS) is spec.secret
    # in a fully-populated config, masked() hides exactly the secrets
    populated = copy.deepcopy(config.DEFAULTS)
    for (s, k) in REGISTRY:
        populated.setdefault(s, {})[k] = "sentinel"
    out = config.masked(populated)
    if spec.secret:
        assert out[section][key] is True  # truthy marker, value gone
    else:
        assert out[section][key] == "sentinel"
    # the input is never mutated
    assert populated[section][key] == "sentinel"


def test_mask_secrets_hides_both_known_secrets():
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["ai"]["api_key"] = "sk-live"
    cfg["model"]["remote_api_key"] = "tok"
    safe = config.masked(cfg)
    assert safe["ai"]["api_key"] is True
    assert safe["model"]["remote_api_key"] is True
    assert "sk-live" not in repr(safe) and "tok" not in repr(safe)
    assert cfg["ai"]["api_key"] == "sk-live"  # original untouched


def test_masked_is_the_historical_name():
    assert config.masked is config.mask_secrets


# -- concern 6: the commented template ------------------------------------------

def test_template_renders_and_parses():
    text = template_text()
    assert text == config.TEMPLATE
    parsed = tomllib.loads(text)  # must be valid TOML
    assert parsed == copy.deepcopy(config.DEFAULTS)  # defaults round-trip
    assert text.startswith("# SayItErmano configuration.")
    for section in config.DEFAULTS:
        assert f"[{section}]" in text


@pytest.mark.parametrize("pair, spec", ALL, ids=IDS)
def test_template_entry_comes_from_the_spec(pair, spec):
    section, key = pair
    text = template_text()
    # the key is documented in its own section with its default value
    assert f"{key} = " in text
    assert f"{key} = {config._toml_value(spec.default)}" in text
    # its description is the rendered comment (first line, at least)
    if spec.description:
        first = spec.description.splitlines()[0]
        assert f"# {first}" in text


def test_template_mentions_every_section_in_order():
    import re
    headers = re.findall(r"^\[([A-Za-z_]+)\]$", template_text(), re.M)
    assert headers == list(config.DEFAULTS)


# -- legacy tables are projections of the registry ------------------------------

def test_setting_tables_derive_from_coercer_kinds():
    """The SETTING_* compatibility tables are pure projections of the
    registry coercers' kind tags - no second source of truth."""
    kinds = {(s, k): getattr(spec.coercer, "kind", None)
             for (s, k), spec in REGISTRY.items()}
    assert config.SETTING_BOOLS == {p for p, kind in kinds.items()
                                    if kind == "bool"}
    assert set(config.SETTING_ENUMS) == {p for p, kind in kinds.items()
                                         if kind == "enum"}
    assert set(config.SETTING_LISTS) == {p for p, kind in kinds.items()
                                         if kind == "list"}
    assert set(config.SETTING_RANGES) == {p for p, kind in kinds.items()
                                          if kind in ("float", "int", "str")}
    for pair, kind in kinds.items():
        if kind == "enum":
            assert config.SETTING_ENUMS[pair] == set(
                config.enum_options(*pair))


def test_ui_range_covers_every_numeric_spec():
    """Every float/int range spec exposes its bounds for widgets; bools,
    enums and structured values expose none."""
    for (s, k), spec in REGISTRY.items():
        kind = getattr(spec.coercer, "kind", None)
        bounds = config.ui_range(s, k)
        if kind in ("float", "int"):
            assert bounds is not None
            lo, hi = bounds
            assert lo <= spec.default <= hi, f"{s}.{k} default out of range"
        elif kind in ("bool", "enum", "str"):
            assert bounds is None, f"{s}.{k} ({kind}) must not carry bounds"
    # spot-check the special custom coercers widgets rely on
    assert config.ui_range("model", "idle_unload_s") == (0, 86400)
    assert config.ui_range("ai", "max_retries") == (0, 10)
    assert config.ui_range("recording", "spoken_send_countdown_s") == (0.0, 5.0)
    assert config.ui_range("general", "language") is None
