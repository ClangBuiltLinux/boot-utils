#!/usr/bin/env bash

trap 'umount "${MOUNT_DIR}" 2>/dev/null; rm -rf "${WORK_DIR}"' INT TERM EXIT

# Prints a message in color
function print_color() {
    # Reset escape code
    RST="\033[0m"
    printf "\n%b%s%b\n" "${1}" "${2}" "${RST}"
}

# Prints an error message in bold red then exits
function die() {
    print_color "\033[01;31m" "${1}"
    exit "${2:-33}"
}

# Prints a warning message in bold yellow
function warn() {
    print_color "\033[01;33m" "${1}"
}

# The script requires root in several places, re-run the script with sudo if necessary
function run_as_root() {
    [[ ${EUID} -eq 0 ]] && return 0
    warn "Script needs to be run as root, invoking sudo on script..."
    echo
    echo "$ exec sudo bash ${*}"
    exec sudo PATH="${PATH}" bash "${@}"
}

# Get user inputs
function get_parameters() {
    DEBIAN=$(dirname "$(readlink -f "${0}")")

    while ((${#})); do
        case ${1} in
            -a | --arch)
                shift
                case ${1} in
                    arm64)
                        DEB_ARCH=${1}
                        OUR_ARCH=${DEB_ARCH}
                        ;;
                    arm)
                        DEB_ARCH=${1}hf
                        OUR_ARCH=${1}
                        ;;
                    ppc64le)
                        DEB_ARCH=ppc64el
                        OUR_ARCH=${1}
                        ;;
                    s390)
                        DEB_ARCH=${1}x
                        OUR_ARCH=${1}
                        ;;
                    x86_64)
                        DEB_ARCH=amd64
                        OUR_ARCH=${1}
                        ;;
                    *) die "${1} is not supported by this script!" ;;
                esac
                ;;
            -h | --help)
                echo
                cat "${DEBIAN}"/README.txt
                echo
                exit 0
                ;;
            -l | --ltp)
                LTP=true
                ;;
            -m | --modules-folder)
                shift
                MODULES_FOLDER=${1}
                [[ -d ${MODULES_FOLDER} ]] || die "${MODULES_FOLDER} specified but it does not exist!"
                ;;
            -p | --password)
                shift
                DEB_PASS=${1}
                ;;
            -u | --user)
                shift
                DEB_USER=${1}
                ;;
            -v | --version)
                shift
                DEB_VERSION=${1}
                ;;
        esac
        shift
    done
}

# Checks if command is available
function is_available() {
    command -v "${1}" &>/dev/null || die "${1} needs to be installed!"
}

# Do some initial checks for environment and configuration
function reality_checks() {
    # Validity checks
    [[ -z ${DEB_ARCH} ]] && die "'-a' is required but not specified!"

    # Some tools are in /usr/sbin or /sbin but they might not be in PATH by default
    [[ ${PATH} =~ /sbin ]] || PATH=${PATH}:/usr/sbin:/sbin
    is_available blkid
    is_available debootstrap
    is_available findmnt
    is_available mkfs.ext4
    is_available qemu-img

    # Default values
    [[ -z ${DEB_VERSION} ]] && DEB_VERSION=bullseye
    [[ -z ${DEB_USER} ]] && DEB_USER=user
    [[ -z ${DEB_PASS} ]] && DEB_PASS=password
    [[ -z ${LTP} ]] && LTP=false
}

# Build image
function create_img() {
    WORK_DIR=$(mktemp -d -p "${DEBIAN}")
    ORIG_USER=$(logname)

    set -x

    # Create the image that we will use and mount it
    IMG=${WORK_DIR}/debian.img
    qemu-img create "${IMG}" 5g
    mkfs.ext4 "${IMG}"
    MOUNT_DIR=${WORK_DIR}/rootfs
    mkdir -p "${MOUNT_DIR}"
    mount -o loop "${IMG}" "${MOUNT_DIR}"

    # Install packages
    PACKAGES=(
        autoconf
        automake
        bash
        bison
        build-essential
        ca-certificates
        flex
        git
        libtool
        m4
        pkg-config
        stress-ng
        sudo
        vim
    )
    debootstrap --arch "${DEB_ARCH}" --include="${PACKAGES[*]//${IFS:0:1}/,}" "${DEB_VERSION}" "${MOUNT_DIR}" || exit ${?}

    # Setup user account
    chroot "${MOUNT_DIR}" bash -c "useradd -m -G sudo ${DEB_USER} -s /bin/bash && echo ${DEB_USER}:${DEB_PASS} | chpasswd"

    # Add fstab so that / mounts as rw instead of ro
    printf "UUID=%s\t/\text4\terrors=remount-ro\t0\t1\n" "$(blkid -o value -s UUID "$(findmnt -n -o SOURCE "${MOUNT_DIR}")")" | tee -a "${MOUNT_DIR}"/etc/fstab

    # Add hostname entry to /etc/hosts so sudo does not complain
    printf "127.0.0.1\t%s\n" "$(uname -n)" | tee -a "${MOUNT_DIR}"/etc/hosts

    # Install some problematic LTP testcases for debugging if requested
    if ${LTP}; then
        LTP_SCRIPT=/home/${DEB_USER}/ltp.sh
        cp -v "${DEBIAN}"/ltp.sh "${MOUNT_DIR}${LTP_SCRIPT}"
        chroot "${MOUNT_DIR}" bash "${LTP_SCRIPT}"
        rm -rf "${LTP_SCRIPT}"
    fi

    # Install modules if requested
    [[ -n ${MODULES_FOLDER} ]] && cp -rv "${MODULES_FOLDER}" "${MOUNT_DIR}"/lib

    # Unmount, move image, and clean up
    umount "${MOUNT_DIR}"
    chown -R "${ORIG_USER}:${ORIG_USER}" "${IMG}"
    mv -v "${IMG}" "${DEBIAN%/*}/images/${OUR_ARCH}"
    rm -rf "${WORK_DIR}"
}

run_as_root "${0}" "${@}"
get_parameters "${@}"
reality_checks
create_img
