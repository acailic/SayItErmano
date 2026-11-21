# One-shot installer (user-space) — Fedora (current Workstation)

STATUS: PENDING
<!-- ^ owner-maintained: PENDING | PASS | FAIL. The harness only ever
     LOWERS this to FAIL when an executed stage fails; only the owner
     marks PASS after the manual checklist below is complete. -->

Paid-launch install gate
([issue #14](https://github.com/acailic/SayItErmano/issues/14)): every route a paying user can take must
work on a machine that is not the developer's. This cell covers
**One-shot installer (user-space)** on **Fedora (current Workstation)**.

Human install command (see the
[install guide](../../../guides/install.md)):

```bash
curl -fsSL https://raw.githubusercontent.com/acailic/SayItErmano/linux/scripts/install-one-shot.sh | bash
```

## Manual checklist — run on a fresh VM, per machine (GPU + CPU)

Automated part first: `python3 scripts/qa_matrix.py check --execute`
on the VM (add `--route oneshot-user`; the root routes need
`sudo python3 scripts/qa_matrix.py check --execute --route oneshot-user`).
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
