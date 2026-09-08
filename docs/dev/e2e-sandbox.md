# End-to-end sandbox testing (no disturbance to the real session)

Run a second SayItErmano instance through the REAL loop — hotkey grab →
record → GPU transcribe → pill → insertion → clipboard verify — on a
virtual X display with a virtual microphone. Verified recipe
(2026-09-08, used for the v0.7.0 feature smokes).

## Setup

1. **Sandbox + virtual display**

       mkdir -p /tmp/sie/{cfg,data,cache}
       Xvfb :99 -screen 0 1920x1080x24 &

   The pill overlay needs a 32-bit visual; with a plain 24-bit screen the
   renderer cannot map — screenshots won't show the pill (log lines like
   `preview stats:` remain the reliable no-preview/present signal). If
   you need pill pixels, add `+extension GLX` and a 32-depth screen list.

2. **Virtual microphone** — `.monitor` sources are EXCLUDED from the
   mic listings by design (speakers are not mics), so a plain null-sink
   monitor cannot be `recording.device`. Hop through
   `module-virtual-source`, which surfaces as a real source:

       pactl load-module module-null-sink sink_name=sie_mic
       pactl load-module module-virtual-source source_name=sie_virt \
           master=sie_mic.monitor
       # "speak" repeatably into it:
       pw-play --target sie_mic some-utterance.wav

3. **Text target** on the virtual display (no window manager — use
   `windowfocus`, not `windowactivate`, which needs EWMH):

       DISPLAY=:99 gedit --new-window /tmp/sie/target.txt &
       DISPLAY=:99 xdotool windowfocus --sync <wid>
       DISPLAY=:99 xdotool mousemove --window <wid> 960 500 click 1

4. **Isolated daemon** — own config/history/lock via XDG env; point the
   socket elsewhere so the production daemon is untouched (since the
   socket-steal refusal, a shared runtime dir would refuse anyway):

       env DISPLAY=:99 \
         XDG_CONFIG_HOME=/tmp/sie/cfg XDG_DATA_HOME=/tmp/sie/data \
         XDG_CACHE_HOME=/tmp/sie/cache \
         SAYITERMANO_NO_APP_SPAWN=1 SAYITERMANO_NO_UPDATE=1 \
         SAYITERMANO_SOCKET=/tmp/sie/sie.sock \
         .venv/bin/python -m fluidvoice daemon --no-sounds &

   Talk to it with the SAME env (CLI socket discovery honors
   `SAYITERMANO_SOCKET`): `control.request("toggle")`, `set-config`,
   `select-model`, and the `transcribe`/`history` API routes.

## Driving + verifying

- Takes: socket `toggle` is deterministic. On bare Xvfb, synthesized
  `xdotool key Control_R` did NOT fire the passive XGrabKey in our runs —
  drive via the socket when the hotkey itself is not the thing under
  test.
- Read what got typed: `DISPLAY=:99 xdotool key ctrl+a ctrl+c;
  DISPLAY=:99 xclip -o -selection clipboard`.
- Screenshot: `DISPLAY=:99 import -window root /tmp/sie/shot.png`.
- Daemon log: the `typed (…)`, `preview stats:`, guard/countdown lines
  are the strongest evidence; keep the foreground stdout in a file.

## Traps (all hit, all real)

- `pkill -f "XDG_CONFIG_HOME=…"` matches NOTHING: env vars are not in
  the command line. Kill by the real cmdline pattern
  (`pkill -f "[f]luidvoice daemon"`) — and beware pkill/pgrep matching
  its own shell (bracket trick).
- Shell `cmd | tail` swallows the piped command's exit code — always
  `echo rc=$?` gates after test runs.
- Pipe-buffered stdout: servers used as smoke props must `print(...,
  flush=True)` or line-reading consumers hang.
- Keep `XDG_RUNTIME_DIR` untouched (PipeWire's socket lives there — that
  is why audio works while everything else is sandboxed).

## Teardown

    pkill -f "[f]luidvoice daemon"   # sandbox instance(s)
    pkill -f "[X]vfb :99"
    pactl unload-module <virtual-source-id> <null-sink-id>
    rm -rf /tmp/sie
    systemctl --user restart sayit-ermano   # only if the unit was touched
