#!/usr/bin/env python3

import argparse
from pathlib import Path
import subprocess

import utils

base_folder = Path(__file__).resolve().parent


def parse_arguments():
    """
    Parses arguments to script.

    Returns:
        A Namespace object containing key values from parser.parse_args()
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help=
        "Instead of immediately shutting down upon successful boot, pass 'init=/bin/sh' to the UML executable to allow interacting with UML via a shell."
    )
    parser.add_argument(
        "-k",
        "--kernel-location",
        required=True,
        type=str,
        help=
        "Path to UML executable ('linux') or kernel build folder to search for executable in. Can be an absolute or relative path."
    )

    return parser.parse_args()


def decomp_rootfs():
    """
    Decompress and get the full path of the initial ramdisk for use with UML.

    Returns:
        rootfs (Path): rootfs Path object containing full path to rootfs.
    """
    rootfs = base_folder.joinpath("images", "x86_64", "rootfs.ext4")

    # This could be 'rootfs.unlink(missing_ok=True)' but that was only added in Python 3.8.
    if rootfs.exists():
        rootfs.unlink()

    utils.check_cmd("zstd")
    subprocess.run(["zstd", "-q", "-d", "{}.zst".format(rootfs), "-o", rootfs],
                   check=True)

    return rootfs


def run_kernel(kernel, rootfs, interactive):
    """
    Run UML command with path to rootfs and additional arguments based on user
    input.

    Parameters:
        * kernel (Path): kernel Path object containing full path to kernel.
        * rootfs (Path): rootfs Path object containing full path to rootfs.
        * interactive (bool): Whether or not to run UML interactively.
    """
    uml_cmd = [kernel.as_posix(), "ubd0={}".format(rootfs.as_posix())]
    if interactive:
        uml_cmd += ["init=/bin/sh"]
    print("$ {}".format(" ".join([str(element) for element in uml_cmd])))
    subprocess.run(uml_cmd, check=True)


if __name__ == '__main__':
    args = parse_arguments()
    kernel = utils.get_full_kernel_path(args.kernel_location, "linux")
    rootfs = decomp_rootfs()

    run_kernel(kernel, rootfs, args.interactive)
