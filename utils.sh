#!/usr/bin/env bash

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

# Expands '-k' to an absolute path to a kernel image if necessary
function get_full_kernel_path() {
    # If '-k' is an path that ends in the kernel image, we can just use it directly
    if [[ ${KERNEL_LOCATION##*/} = "${KIMAGE:=zImage}" ]]; then
        KERNEL=${KERNEL_LOCATION}
    # If not though, we need to find it based on the kernel build directory
    else
        # If the image is an uncompressed vmlinux or a UML image, it is in the
        # root of the build folder
        # Otherwise, it is in the architecture's boot directory
        [[ ${KIMAGE} == "vmlinux" || ${KIMAGE} == "linux" ]] || BOOT_DIR=arch/${ARCH}/boot/
        KERNEL=${KERNEL_LOCATION}/${BOOT_DIR}${KIMAGE}
    fi
    [[ -f ${KERNEL} ]] || die "${KERNEL} does not exist!"
}
