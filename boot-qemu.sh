#!/usr/bin/env bash

# Root of the repo
BASE=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)

function pretty_print() {
    printf "%b%s\033[0m" "${1}" "${2}"
    shift 2
    while ((${#})); do
        printf "%b" "${1}"
        shift
    done
    printf '\n'
}

function green() {
    pretty_print "\033[01;32m" "${@}"
}

function red() {
    pretty_print "\033[01;31m" "${@}"
}

# Prints an error message in bold red then exits
function die() {
    red "${@}"
    exit 1
}

# Check that a binary is found
function checkbin() {
    command -v "${1}" &>/dev/null || die "${1} could not be found, please install it!"
}

# Parse inputs to the script
function parse_parameters() {
    while ((${#})); do
        case ${1} in
            -a | --arch | --architecture)
                shift
                case ${1} in
                    arm32_v5 | arm32_v6 | arm32_v7 | arm64 | arm64be | mips | mipsel | ppc32 | ppc64 | ppc64le | riscv | s390 | x86 | x86_64) ARCH=${1} ;;
                    *) die "Invalid --arch value '${1}'" ;;
                esac
                ;;

            -d | --debug)
                set -x
                ;;

            -g | --gdb)
                GDB=true
                INTERACTIVE=true
                ;;

            -h | --help)
                echo
                cat "${BASE}"/README.txt
                echo
                exit 0
                ;;

            -i | --interactive | --shell)
                INTERACTIVE=true
                ;;

            -k | --kernel-location)
                shift && KERNEL_LOCATION=${1}
                ;;

            -t | --timeout)
                shift && TIMEOUT=${1}
                ;;

            --use-cbl-qemu)
                USE_CBL_QEMU=true
                ;;

            *)
                die "Invalid parameter '${1}'"
                ;;
        esac
        shift
    done
}

# Sanity check parameters and required tools
function sanity_check() {
    # Kernel build folder and architecture are required paramters
    [[ -z ${ARCH} ]] && die "Architecture ('-a') is required but not specified!"
    [[ -z ${KERNEL_LOCATION} ]] && die "Kernel image or kernel build folder ('-k') is required but not specified!"

    # KERNEL_LOCATION could be a relative path; turn it into an absolute one with readlink
    KERNEL_LOCATION=$(readlink -f "${KERNEL_LOCATION}")

    # Make sure zstd is install
    checkbin zstd
}

# Boot QEMU
function setup_qemu_args() {
    # All arm32_* options share the same rootfs, under images/arm
    [[ ${ARCH} =~ arm32 ]] && ARCH_RTFS_DIR=arm

    IMAGES_DIR=${BASE}/images/${ARCH_RTFS_DIR:-${ARCH}}
    ROOTFS=${IMAGES_DIR}/rootfs.cpio

    APPEND_STRING=""
    if ${INTERACTIVE:=false}; then
        APPEND_STRING+="rdinit=/bin/sh "
    fi
    if ${GDB:=false}; then
        APPEND_STRING+="nokaslr "
    fi

    case ${ARCH} in
        arm32_v5)
            ARCH=arm
            DTB=aspeed-bmc-opp-palmetto.dtb
            QEMU_ARCH_ARGS=(
                -machine palmetto-bmc
                -no-reboot
            )
            QEMU=(qemu-system-arm)
            ;;

        arm32_v6)
            ARCH=arm
            DTB=aspeed-bmc-opp-romulus.dtb
            QEMU_ARCH_ARGS=(
                -machine romulus-bmc
                -no-reboot
            )
            QEMU=(qemu-system-arm)
            ;;

        arm32_v7)
            ARCH=arm
            APPEND_STRING+="console=ttyAMA0 "
            QEMU_ARCH_ARGS=(
                -machine virt
                -no-reboot
            )
            QEMU=(qemu-system-arm)
            ;;

        arm64 | arm64be)
            ARCH=arm64
            KIMAGE=Image.gz
            APPEND_STRING+="console=ttyAMA0 "
            QEMU_ARCH_ARGS=(
                -cpu max
                -machine "virt,gic-version=max"
            )
            if [[ "$(uname -m)" = "aarch64" && -e /dev/kvm ]]; then
                QEMU_ARCH_ARGS+=(-enable-kvm)
            else
                QEMU_ARCH_ARGS+=(-machine "virtualization=true")
            fi
            QEMU=(qemu-system-aarch64)
            ;;

        mips | mipsel)
            KIMAGE=vmlinux
            QEMU_ARCH_ARGS=(
                -cpu 24Kf
                -machine malta
            )
            QEMU=(qemu-system-"${ARCH}")
            ARCH=mips
            ;;

        ppc32)
            ARCH=powerpc
            KIMAGE=uImage
            APPEND_STRING+="console=ttyS0 "
            QEMU_ARCH_ARGS=(
                -machine bamboo
                -no-reboot
            )
            QEMU_RAM=128m
            QEMU=(qemu-system-ppc)
            ;;

        ppc64)
            ARCH=powerpc
            KIMAGE=vmlinux
            QEMU_ARCH_ARGS=(
                -machine pseries
                -vga none
            )
            QEMU_RAM=1G
            QEMU=(qemu-system-ppc64)
            ;;

        ppc64le)
            ARCH=powerpc
            KIMAGE=zImage.epapr
            QEMU_ARCH_ARGS=(
                -device "ipmi-bmc-sim,id=bmc0"
                -device "isa-ipmi-bt,bmc=bmc0,irq=10"
                -L "${IMAGES_DIR}/" -bios skiboot.lid
                -machine powernv
            )
            QEMU_RAM=2G
            QEMU=(qemu-system-ppc64)
            ;;

        riscv)
            KIMAGE=Image
            DEB_BIOS=/usr/lib/riscv64-linux-gnu/opensbi/qemu/virt/fw_jump.elf
            [[ -f ${DEB_BIOS} && -z ${BIOS} ]] && BIOS=${DEB_BIOS}
            QEMU_ARCH_ARGS=(
                -bios "${BIOS:-default}"
                -M virt
            )
            QEMU=(qemu-system-riscv64)
            ;;

        s390)
            KIMAGE=bzImage
            QEMU_ARCH_ARGS=(-M s390-ccw-virtio)
            QEMU=(qemu-system-s390x)
            ;;

        x86 | x86_64)
            KIMAGE=bzImage
            APPEND_STRING+="console=ttyS0 "
            # Use KVM if the processor supports it and the KVM module is loaded (i.e. /dev/kvm exists)
            if [[ $(grep -c -E 'vmx|svm' /proc/cpuinfo) -gt 0 && -e /dev/kvm ]]; then
                QEMU_ARCH_ARGS=(
                    -cpu host
                    -d "unimp,guest_errors"
                    -enable-kvm
                    -smp "$(nproc)"
                )
            fi
            case ${ARCH} in
                x86) QEMU=(qemu-system-i386) ;;
                x86_64) QEMU=(qemu-system-x86_64) ;;
            esac
            ;;
    esac
    if ${USE_CBL_QEMU:-false} && [[ ${ARCH} = "riscv" || ${ARCH} = "s390" ]]; then
        QEMU_BINARIES=${BASE}/qemu-binaries

        green "Downloading or updating qemu-binaries..."
        [[ -d ${QEMU_BINARIES} ]] || git clone https://github.com/ClangBuiltLinux/qemu-binaries "${QEMU_BINARIES}"
        git -C "${QEMU_BINARIES}" pull --rebase

        QEMU_BIN=${QEMU_BINARIES}/bin
        QEMU_BINARY=${QEMU_BIN}/${QEMU[*]}
        zstd -q -d "${QEMU_BINARY}".zst -o "${QEMU_BINARY}" || die "Error decompressing ${QEMU[*]}"
        export PATH=${QEMU_BIN}:${PATH}
    fi
    checkbin "${QEMU[*]}"

    # If '-k' is an path that ends in the kernel image, we can just use it directly
    if [[ ${KERNEL_LOCATION##*/} = "${KIMAGE:=zImage}" ]]; then
        KERNEL=${KERNEL_LOCATION}
    # If not though, we need to find it based on the kernel build directory
    else
        # If the image is an uncompressed vmlinux, it is in the root of the build folder
        # Otherwise, it is in the architecture's boot directory
        [[ ${KIMAGE} == "vmlinux" ]] || BOOT_DIR=arch/${ARCH}/boot/
        KERNEL=${KERNEL_LOCATION}/${BOOT_DIR}${KIMAGE}
    fi
    [[ -f ${KERNEL} ]] || die "${KERNEL} does not exist!"
    if [[ -n ${DTB} ]]; then
        # If we are in a boot folder, look for them in the dts folder in it
        if [[ $(basename "${KERNEL%/*}") = "boot" ]]; then
            DTB_FOLDER=dts/
        # Otherwise, assume there is a dtbs folder in the same folder as the kernel image (tuxmake)
        else
            DTB_FOLDER=dtbs/
        fi
        DTB=${KERNEL%/*}/${DTB_FOLDER}${DTB}
        [[ -f ${DTB} ]] || die "${DTB##*/} is required for booting but it could not be found at ${DTB}!"
        QEMU_ARCH_ARGS+=(-dtb "${DTB}")
    fi
}

# Invoke QEMU
function invoke_qemu() {
    rm -rf "${ROOTFS}"
    zstd -q -d "${ROOTFS}".zst -o "${ROOTFS}"

    green "QEMU location: " "$(dirname "$(command -v "${QEMU[*]}")")" '\n'
    green "QEMU version: " "$("${QEMU[@]}" --version | head -n1)" '\n'

    [[ -z ${QEMU_RAM} ]] && QEMU_RAM=512m
    if ${GDB:=false}; then
        while true; do
            if lsof -i:1234 &>/dev/null; then
                red "Port :1234 already bound to. QEMU already running?"
                exit 1
            fi
            green "Starting QEMU with GDB connection on port 1234..."
            # Note: no -serial mon:stdio
            "${QEMU[@]}" \
                "${QEMU_ARCH_ARGS[@]}" \
                -append "${APPEND_STRING%* }" \
                -display none \
                -initrd "${ROOTFS}" \
                -kernel "${KERNEL}" \
                -m "${QEMU_RAM}" \
                -nodefaults \
                -s -S &
            QEMU_PID=$!
            green "Starting GDB..."
            "${GDB_BIN:-gdb-multiarch}" "${KBUILD_DIR}/vmlinux" -ex "target remote :1234"
            red "Killing QEMU..."
            kill -9 "${QEMU_PID}"
            wait "${QEMU_PID}" 2>/dev/null
            while true; do
                read -rp "Rerun [Y/n/?] " yn
                case $yn in
                    [Yy]*) break ;;
                    [Nn]*) exit 0 ;;
                    *) break ;;
                esac
            done
        done
    fi

    ${INTERACTIVE} || QEMU=(timeout --foreground "${TIMEOUT:=3m}" unbuffer "${QEMU[@]}")
    set -x
    "${QEMU[@]}" \
        "${QEMU_ARCH_ARGS[@]}" \
        -append "${APPEND_STRING%* }" \
        -display none \
        -initrd "${ROOTFS}" \
        -kernel "${KERNEL}" \
        -m "${QEMU_RAM}" \
        -nodefaults \
        -serial mon:stdio
    RET=${?}
    set +x

    return ${RET}
}

parse_parameters "${@}"
sanity_check
setup_qemu_args
invoke_qemu
