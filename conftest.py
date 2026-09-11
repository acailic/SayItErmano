# Repository-root conftest: plugin registration for EVERY pytest run
# (quality plan Q2, finding E2). The runner-hygiene gate must load even
# for focused single-file runs and in every pytest-xdist worker — a
# pytest_plugins entry in the ROOT conftest is the supported way to do
# that (non-root conftests may not declare pytest_plugins).
#
# "tests._runner_hygiene" resolves because this project mandates
# `python -m pytest` (AGENTS.md; the tier script and justfile use it),
# which puts the repo root first on sys.path.
pytest_plugins = ["tests._runner_hygiene"]
