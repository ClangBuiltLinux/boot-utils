#!/usr/bin/env bash

BASE=$(dirname "$(readlink -f "$0")")
source "$BASE"/utils.sh

function parse_parameters() {
    while (($#)); do
        case $1 in
            -i | --interactive | --shell)
                kernel_args=(init=/bin/sh)
                ;;
            -k | --kernel-location)
                shift
                KERNEL_LOCATION=$1
                ;;
        esac
        shift
    done
}

function reality_check() {
    [[ -z $KERNEL_LOCATION ]] && die "Kernel image or kernel build folder ('-k') is required but not specified!"
    KIMAGE=linux get_full_kernel_path
}

function decomp_rootfs() {
    rootfs=$BASE/images/x86_64/rootfs.ext4
    rm -rf "$rootfs"
    zstd -q -d "$rootfs".zst -o "$rootfs"
}

function execute_kernel() {
    # exec is needed to avoid a "Killed" message when the kernel shuts down.
    # Nothing runs after this command so it is okay for it to replace this process.
    exec "$KERNEL" ubd0="$rootfs" "${kernel_args[@]}"
}

parse_parameters "$@"
reality_check
decomp_rootfs
execute_kernel
