# Security Policy

SayItErmano is an unofficial community Linux port of FluidVoice
(macOS); this repository is not affiliated with the upstream project.

## Supported versions

Only the latest release line receives security fixes. Please update
before reporting (`sayit-ermano update` prints the upgrade command for
your install method).

## Reporting a vulnerability

Please report privately — do NOT open a public GitHub issue for
security problems.

1. Use GitHub's **"Report a vulnerability"** action on the
   [Security advisories page](https://github.com/acailic/SayItErmano/security/advisories/new),
   or
2. Email the maintainer via the address listed on the GitHub profile
   of [@acailic](https://github.com/acailic), with `[SayItErmano
   security]` in the subject.

Include reproduction steps, affected version (`sayit-ermano --version`),
and your distro/session type (X11 or Wayland). You should receive a
response within 7 days. Coordinated disclosure: we ask for up to 90 days
before public disclosure and will credit reporters unless you prefer
to remain anonymous.

## Scope notes (design, not bugs)

- The control socket is a UNIX domain socket under the user's runtime
  directory (`$XDG_RUNTIME_DIR/sayit-ermano.sock`) — no TCP listener
  exists by design; local filesystem access equals the user's own
  privileges.
- The optional remote STT backend (`model.remote_url`) is OFF by
  default; nothing leaves the machine while it is empty. API keys are
  masked in every socket/read surface and never logged.
- Command mode never auto-executes: every command requires an explicit
  hotkey confirmation, destructive ones a two-press strong confirm.
