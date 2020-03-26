#!/usr/bin/env bash

set -eux

BASE=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
VER=${1}
TMP=$(mktemp -d)

trap 'rm -rf "${TMP}"' EXIT INT TERM

# Build skiboot
cd "${TMP}"
curl -LSs https://github.com/open-power/skiboot/archive/v"${VER}".tar.gz | tar xzf -
cd skiboot-"${VER}"
CROSS=${CROSS:-powerpc64le-linux-gnu-} SKIBOOT_VERSION=v${VER} make -j"$(nproc)"
cp -v skiboot.lid "${BASE}"
rm -rf "${PWD}"
