# Architecture Decision Records

Numbered, immutable once accepted; a new decision that reverses an old
one gets a new ADR that marks the old one Superseded.

| ADR | Decision | Status |
|---|---|---|
| [ADR-0001](ADR-0001-no-tcp.md) | No TCP — the Unix control socket is the only external interface | Accepted |
| [ADR-0002](ADR-0002-locked-jsonl-history.md) | History stays JSONL, made transactional with a sidecar flock | Accepted |
| [ADR-0003](ADR-0003-runtime-task-ownership.md) | Runtime tasks are owned, tracked and cancellable | Accepted |
| [ADR-0004](ADR-0004-ubuntu-deb-contract.md) | The deb is an Ubuntu 24.04 / x86_64 / Python 3.12 contract | Accepted |

Context: what shipped is [STATUS.md](../STATUS.md); what is next is
[ROADMAP.md](../ROADMAP.md); vocabulary is the
[glossary](../glossary.md); the evidence base is
[research/](../research/).
