#!/usr/bin/env bash

# Root of the repo
BASE=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)


# Prints an error message in bold red then exits
function die() {
    printf "\n\033[01;31m%s\033[0m\n" "${1}"
    exit 1
}


# Check that a binary is found
function checkbin() {
    command -v "${1}" &>/dev/null || die "${1} could not be found, please install it!"
}


# Parse inputs to the script
function parse_parameters() {
    while (( ${#} )); do
        case ${1} in
            -a|--arch|--architecture)
                shift
                case ${1} in
                    arm32_v5|arm32_v6|arm32_v7|arm64|mips|mipsel|ppc32|ppc64|ppc64le|x86_64) ARCH=${1} ;;
                    *) die "Invalid --arch value '${1}'" ;;
                esac ;;

            -d|--debug)
                set -x ;;

            -g|--gdb)
                GDB=true
                INTERACTIVE=true ;;

            -h|--help)
                echo
                cat "${BASE}"/README.txt
                echo
                exit 0 ;;

            -i|--interactive|--shell)
                INTERACTIVE=true ;;

            -k|--kbuild-folder)
                shift && KBUILD_DIR=${1} ;;

            -t|--timeout)
                shift && TIMEOUT=${1} ;;

            *)
                die "Invalid parameter '${1}'" ;;
        esac
        shift
    done
}


# Sanity check parameters and required tools
function sanity_check() {
    # Kernel build folder and architecture are required paramters
    [[ -z ${ARCH} ]] && die "Architecture ('-a') is required but not specified!"
    [[ -z ${KBUILD_DIR} ]] && die "Kernel build folder ('-k') is required but not specified!"

    # KBUILD_DIR could be a relative path; turn it into an absolute one with readlink
    KBUILD_DIR=$(readlink -f "${KBUILD_DIR}")

    # Let the user know if the kernel build folder does not exist
    [[ -d ${KBUILD_DIR} ]] || die "${KBUILD_DIR} does not exist!"

    # Make sure zstd is install
    checkbin zstd
}


# Decompress rootfs images
function decomp_rootfs() {
    # All arm32_* options share the same rootfs, under images/arm
    [[ ${ARCH} =~ arm32 ]] && ARCH_RTFS_DIR=arm

    IMAGES_DIR=${BASE}/images/${ARCH_RTFS_DIR:-${ARCH}}
    ROOTFS=${IMAGES_DIR}/rootfs.cpio

    rm -rf "${ROOTFS}"
    zstd -d "${ROOTFS}".zst -o "${ROOTFS}"
}


# Boot QEMU
function setup_qemu_args() {
    if ${INTERACTIVE:=false}; then
        RDINIT=" rdinit=/bin/sh"
        APPEND_RDINIT=( -append "${RDINIT}" )
    fi

    case ${ARCH} in
        arm32_v5)
            ARCH=arm
            QEMU_ARCH_ARGS=( "${APPEND_RDINIT[@]}"
                             -dtb "${KBUILD_DIR}"/arch/arm/boot/dts/aspeed-bmc-opp-palmetto.dtb
                             -machine palmetto-bmc
                             -no-reboot )
            QEMU=( qemu-system-arm ) ;;

        arm32_v6)
            ARCH=arm
            QEMU_ARCH_ARGS=( "${APPEND_RDINIT[@]}"
                             -dtb "${KBUILD_DIR}"/arch/arm/boot/dts/aspeed-bmc-opp-romulus.dtb
                             -machine romulus-bmc
                             -no-reboot )
            QEMU=( qemu-system-arm ) ;;

        arm32_v7)
            ARCH=arm
            QEMU_ARCH_ARGS=( -append "console=ttyAMA0${RDINIT}"
                             -machine virt
                             -no-reboot )
            QEMU=( qemu-system-arm ) ;;

        arm64)
            KIMAGE=Image.gz
            QEMU_ARCH_ARGS=( -append "console=ttyAMA0${RDINIT}"
                             -cpu cortex-a57
                             -machine virt )
            QEMU=( qemu-system-aarch64 ) ;;

        mips|mipsel)
            KIMAGE=vmlinux
            QEMU_ARCH_ARGS=( "${APPEND_RDINIT[@]}"
                             -cpu 24Kf
                             -machine malta )
            QEMU=( qemu-system-"${ARCH}" )
            ARCH=mips ;;

        ppc32)
            ARCH=powerpc
            QEMU_ARCH_ARGS=( -append "console=ttyS0${RDINIT}"
                             -machine bamboo
                             -no-reboot )
            QEMU_RAM=128m
            QEMU=( qemu-system-ppc ) ;;

        ppc64)
            ARCH=powerpc
            KIMAGE=vmlinux
            QEMU_ARCH_ARGS=( "${APPEND_RDINIT[@]}"
                             -machine pseries
                             -vga none )
            QEMU_RAM=1G
            QEMU=( qemu-system-ppc64 ) ;;

        ppc64le)
            ARCH=powerpc
            KIMAGE=zImage.epapr
            QEMU_ARCH_ARGS=( "${APPEND_RDINIT[@]}"
                             -device "ipmi-bmc-sim,id=bmc0"
                             -device "isa-ipmi-bt,bmc=bmc0,irq=10"
                             -L "${IMAGES_DIR}/" -bios skiboot.lid
                             -machine powernv )
            QEMU_RAM=2G
            QEMU=( qemu-system-ppc64 ) ;;

        x86_64)
            KIMAGE=bzImage
            QEMU_ARCH_ARGS=( -append "console=ttyS0${RDINIT}" )
            # Use KVM if the processor supports it (first part) and the KVM module is loaded (second part)
            [[ $(grep -c -E 'vmx|svm' /proc/cpuinfo) -gt 0 && $(lsmod 2>/dev/null | grep -c kvm) -gt 0 ]] && \
                QEMU_ARCH_ARGS=( "${QEMU_ARCH_ARGS[@]}" -cpu host -d "unimp,guest_errors" -enable-kvm )
            QEMU=( qemu-system-x86_64 ) ;;
    esac
    checkbin "${QEMU[*]}"

    [[ ${KIMAGE:=zImage} = "vmlinux" ]] || BOOT_DIR=arch/${ARCH}/boot/
    KERNEL=${KBUILD_DIR}/${BOOT_DIR}${KIMAGE}
    [[ -f ${KERNEL} ]] || die "${KERNEL} does not exist!"
}


# Invoke QEMU
function invoke_qemu() {
    ${INTERACTIVE} || QEMU=( timeout "${TIMEOUT:=3m}" unbuffer "${QEMU[@]}" )
    if ${GDB:=false}; then
        # Print message in bold green
        printf '\033[01;32m'
        echo
        echo "Starting QEMU with GDB connection on port 1234..."
        echo
        echo "Use:"
        echo
        printf '\ttarget remote :1234\n'
        echo
        echo "to connect"
        echo
        printf '\033[0m'
        QEMU=( "${QEMU[@]}" -s -S )
    fi

    set -x
    "${QEMU[@]}" \
        "${QEMU_ARCH_ARGS[@]}" \
        -display none \
        -initrd "${ROOTFS}" \
        -kernel "${KERNEL}" \
        -m "${QEMU_RAM:=512m}" \
        -nodefaults \
        -serial mon:stdio
    RET=${?}
    set +x

    return ${RET}
}


parse_parameters "${@}"
sanity_check
decomp_rootfs
setup_qemu_args
invoke_qemu
