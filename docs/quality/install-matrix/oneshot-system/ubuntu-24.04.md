# One-shot installer (--system) — Ubuntu 24.04

STATUS: PENDING
<!-- ^ owner-maintained: PENDING | PASS | FAIL. The harness only ever
     LOWERS this to FAIL when an executed stage fails; only the owner
     marks PASS after the manual checklist below is complete. -->

Paid-launch install gate
([issue #14](https://github.com/acailic/SayItErmano/issues/14)): every route a paying user can take must
work on a machine that is not the developer's. This cell covers
**One-shot installer (--system)** on **Ubuntu 24.04**.

The installer's --system path is apt-based; on Fedora it is expected to stop at the package step — record the outcome and the pipx fallback if so.

Human install command (see the
[install guide](../../../guides/install.md)):

```bash
curl -fsSL https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh | bash -s -- --system
```

## Manual checklist — run on a fresh VM, per machine (GPU + CPU)

Automated part first: `python3 scripts/qa_matrix.py check --execute`
on the VM (add `--route oneshot-system`; the root routes need
`sudo python3 scripts/qa_matrix.py check --execute --route oneshot-system`).
Then tick, once per machine class:

- [ ] GPU machine: install succeeds end to end
- [ ] first-run onboarding window appears once; the 3-second tryout works
- [ ] real dictation: Right Ctrl, speak, Right Ctrl — polished text
      lands in another app
- [ ] update check: `sayit-ermano update` prints the right command for
      this route; updating to the NEXT release works
- [ ] CPU machine: install + onboarding + one real dictation
      (transcription falls back to CPU int8, `doctor` confirms)

Record date + commit SHA + machine per run, appended at the bottom
(house rule from [desktop-matrix](../../../dev/desktop-matrix.md)).

## Automated harness runs

<!-- qa-matrix automated runs BEGIN (harness-owned; do not edit) -->
_No automated run recorded yet._
<!-- qa-matrix automated runs END -->
