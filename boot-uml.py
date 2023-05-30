#!/usr/bin/env python3
# pylint: disable=invalid-name

import argparse
import subprocess

import utils


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


def run_kernel(kernel_image, rootfs, interactive):
    """
    Run UML command with path to rootfs and additional arguments based on user
    input.

    Parameters:
        * kernel_image (Path): kernel Path object containing full path to kernel.
        * rootfs (Path): rootfs Path object containing full path to rootfs.
        * interactive (bool): Whether or not to run UML interactively.
    """
    uml_cmd = [kernel_image, f"ubd0={rootfs}"]
    if interactive:
        uml_cmd += ["init=/bin/sh"]
    print(f"$ {' '.join([str(element) for element in uml_cmd])}")
    subprocess.run(uml_cmd, check=True)


if __name__ == '__main__':
    args = parse_arguments()
    kernel = utils.get_full_kernel_path(args.kernel_location, "linux")
    initrd = utils.prepare_initrd('x86_64', rootfs_format='ext4')

    run_kernel(kernel, initrd, args.interactive)
