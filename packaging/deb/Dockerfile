# Pinned build environment for the SayItErmano .deb.
#
# THE DEB CONTRACT (plan P0.6): Ubuntu 24.04 / x86_64 / Python 3.12 —
# nothing else is a supported target. The deb bundles a venv whose python
# symlinks the system interpreter, so whatever machine builds it stamps
# its own Python minor into the package. Release artifacts therefore come
# out of THIS pinned image, never off a random dev desktop; dependencies
# are fixed by the committed, reviewable lock packaging/deb/constraints.txt.
#
# Build (from the repo root):
#
#   docker build -t sayit-ermano-deb -f packaging/deb/Dockerfile .
#   mkdir -p dist && docker run --rm -v "$PWD/dist:/out" sayit-ermano-deb
#   sudo apt install ./dist/sayit-ermano_<ver>-1_amd64.deb
#
# Digest-pinning the base image (recommended for release builds — a moving
# tag still tracks Ubuntu's security updates between releases):
#
#   docker pull ubuntu:24.04
#   docker images --digests ubuntu | awk '$2 == "24.04" {print $3}'  # sha256:…
#   docker build --build-arg UBUNTU_IMAGE=ubuntu:24.04@sha256:<digest> \
#       -t sayit-ermano-deb -f packaging/deb/Dockerfile .
#
# Record the digest you released with in the release notes. build-deb.sh
# independently refuses to run outside Linux/CPython 3.12/x86_64, so a wrong
# base image cannot produce a contract-violating deb silently.
ARG UBUNTU_IMAGE=ubuntu:24.04
FROM ${UBUNTU_IMAGE}

# dpkg-deb ships in the base image (xz payload needs xz-utils); fakeroot is
# the first choice of build-deb.sh's pack step; python3-venv provides the
# ensurepip used to build the bundled venv.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fakeroot \
        python3-venv \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

# Needs network at run time (pip fetches the locked wheels from PyPI and
# verifies their hashes when constraints.txt carries them). The container
# has no CUDA stack: the bundled venv falls back to CPU (faster-whisper
# int8) exactly like a CUDA-less install of the deb does.
CMD ["bash", "-c", "packaging/build-deb.sh && mkdir -p /out && cp -v dist/*.deb /out/"]
