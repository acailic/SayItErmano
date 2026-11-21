# The signed apt repo (`site/apt`)

The update channel for deb installs (issue #22): a reprepro-managed,
GPG-signed apt suite `stable` that lives in this repository at `site/apt/`
and is served by GitHub Pages at:

```
https://acailic.github.io/SayItErmano/apt
```

Installing the official `.deb` configures this repo automatically (it
ships `/etc/apt/keyrings/sayit-ermano.gpg` + the sources-list entry as
conffiles — see `packaging/build-deb.sh` §4b), so from then on:

```bash
sudo apt update && sudo apt upgrade sayit-ermano
```

moves the machine to each new release with config (`~/.config/sayit-ermano`)
and history untouched (the package never writes there). The same channel
powers `scripts/install-one-shot.sh --update` for system installs.

## How it publishes

`release-publish` (the manual release workflow) runs, after the GitHub
release is created:

```bash
bash packaging/apt/update-repo.sh site/apt dist/*.deb
```

which signs and verifies the repo (sandboxed `apt-get update` + policy +
download against a `file://` copy), then the workflow commits `site/apt/`
to `linux` — the Pages workflow (triggered by `site/**` changes) deploys
it. Nothing here ever runs automatically outside that manual flow.

**Single-version policy:** only the newest release stays in the pool
(`update-repo.sh` drops older `sayit-ermano` versions before including
the new deb). Upgrades always work; downgrades are not served. This
bounds the repo — and the GitHub Pages site, capped at 1 GB total /
100 MB per file — to roughly one deb. When a release deb approaches the
per-file cap, the channel must move off Pages (revisit then).

## One-time provisioning (the signing key)

The repo is signed by a dedicated keypair. The **public** half is
committed here as `sayit-ermano.asc` (the deb build requires it); the
**private** half lives ONLY in the GitHub secret `APT_REPO_GPG_KEY`.

```bash
# 1. generate (no passphrase: it is a dedicated repo-signing key whose
#    secret already rests in CI storage; a passphrase would only complicate
#    the unattended release flow)
gpg --batch --passphrase '' --quick-generate-key \
    "SayItErmano apt repo <sayit-ermano@users.noreply.github.com>" \
    rsa3072 sign 0

# 2. commit the public half
gpg --armor --export "SayItErmano apt repo" > packaging/apt/sayit-ermano.asc
git add packaging/apt/sayit-ermano.asc && git commit -m "apt: signing key (public)"
# 3. hand the private half to the release workflow (never commit it)
gpg --armor --export-secret-keys "SayItErmano apt repo" | \
    gh secret set APT_REPO_GPG_KEY
```

Until this is done, release-prepare's locked deb build fails (the deb
must embed the keyring) and release-publish fails its apt step — that is
the loud, intended state; `build-deb.sh` offers `DEB_ALLOW_NO_APT_REPO=1`
for local dev builds only, and release-publish accepts an explicit
`skip-apt-reason` input to record a one-off waiver.

## Local test of the channel

```bash
sudo apt install reprepro        # + gnupg
gpg --batch --passphrase '' --quick-generate-key "test key" rsa2048 sign 0
gpg --armor --export "test key" > packaging/apt/sayit-ermano.asc   # temporary
bash packaging/apt/update-repo.sh /tmp/apt-test dist/sayit-ermano_<ver>_amd64.deb
git checkout packaging/apt/sayit-ermano.asc   # restore the real key
```

`update-repo.sh` verifies the result like a client (signature, candidate
version, pool hashes), so a green run is the strongest offline evidence
the channel works. Full `apt upgrade` VM evidence lives in the #14 QA
matrix under `docs/quality/`.
