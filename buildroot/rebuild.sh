#!/usr/bin/env bash
# Takes a list of architectures to build images for as the parameter

# Prints an error message in bold red then exits
function die() {
    printf "\n\033[01;31m%s\033[0m\n" "${1}"
    exit 1
}

function download_br() {
    mkdir -p src
    TARBALL=buildroot-${BUILDROOT_VERSION}.tar.gz
    rm -f "${TARBALL}"
    curl -LO https://buildroot.org/downloads/"${TARBALL}"
    sha256sum --quiet -c "${TARBALL}".sha256 || die "Downloaded tarball's hash does not match known good one! Please try redownloading."
    tar -xzf "${TARBALL}" -C src --strip-components=1
    rm -f "${TARBALL}"
}

# Make sure we don't have any unset variables
set -u

# Move into the folder that contains this script
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" || exit 1

# Make sure the user has zstd installed
command -v zstd &>/dev/null || die "zstd could not be found on your system, please install it!"

# Generate list of configs to build
CONFIGS=()
[[ ${#} -eq 0 ]] && die "Please specify the configs that you want to build as parameters to this script!"
while ((${#})); do
    case ${1} in
        all) for CONFIG in *.config; do CONFIGS+=("../${CONFIG}"); done ;;
        arm64 | arm64be | arm | mips | mipsel | ppc32 | ppc64 | ppc64le | riscv | s390 | x86 | x86_64) CONFIGS+=("../${1}.config") ;;
        *) die "Unknown parameter '${1}', exiting!" ;;
    esac
    shift
done

# Download latest LTS buildroot release
BUILDROOT_VERSION=2020.11.2
if [[ -d src ]]; then
    if [[ $(cd src && make print-version | cut -d - -f 1 2>/dev/null) != "${BUILDROOT_VERSION}" ]]; then
        rm -rf src
        download_br
    fi
else
    download_br
fi
cd src || exit 1

# Build the images for the architectures requested
for CONFIG in "${CONFIGS[@]}"; do
    # Clean up artifacts from the last build
    make clean

    BR2_DEFCONFIG=${CONFIG} make defconfig
    if [[ -n ${EDITCONFIG:-} ]]; then
        make menuconfig
        make savedefconfig
    fi

    # Build images
    make -j"$(nproc)"

    # Get the architecture from the name of the config: ../<arch>.config
    # basename strips ../
    # ${CONFIG//.config} strips .config
    ARCH=$(basename "${CONFIG//.config/}")

    # Make sure images folder exists
    IMAGES_FOLDER=../../images/${ARCH}
    [[ ! -d ${IMAGES_FOLDER} ]] && mkdir -p "${IMAGES_FOLDER}"

    # Copy new images
    # Make sure images exist before moving them
    IMAGES=("output/images/rootfs.cpio")
    for IMAGE in "${IMAGES[@]}"; do
        [[ -f ${IMAGE} ]] || die "${IMAGE} could not be found! Did the build error?"
        zstd -f -19 "${IMAGE}" -o "${IMAGES_FOLDER}/${IMAGE##*/}.zst" || die "Compressing ${IMAGE##*/} failed!"
    done
done
