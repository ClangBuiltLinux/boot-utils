#!/usr/bin/env python3

from pathlib import Path
import shutil
import sys


def check_cmd(cmd):
    """
    Checks if external command is available in PATH, erroring out if it is not
    available.

    Parameters:
        cmd (str): External command name or path.
    """
    if not shutil.which(cmd):
        die(f"The external command '{cmd}' is needed but it could not be found in PATH, please install it!"
            )


def die(string):
    """
    Prints a string in bold red then exits with an error code of 1.

    Parameters:
        string (str): String to print in red; prefixed with "ERROR: "
                      automatically.
    """
    red(f"ERROR: {string}")
    sys.exit(1)


def get_full_kernel_path(kernel_location, image, arch=None):
    """
    Get the full path to a kernel image based on the architecture and image
    name if necessary.

    Parameters:
        kernel_location (str): Absolute or relative path to kernel image or
                               kernel build folder.
        image (str): Kernel image name.
        arch (str, optional): Architecture name according to Kbuild; should be
                              the parent of the "boot" folder containing the
                              kernel image (default: None).
    """
    kernel_location = Path(kernel_location)

    # If '-k' is a file, we can just use it directly
    if kernel_location.is_file():
        kernel = kernel_location
    # If not, we need to find it based on the kernel build directory
    else:
        # If the image is an uncompressed vmlinux or a UML image, it is in the
        # root of the build folder
        if image in ("vmlinux", "linux"):
            kernel = kernel_location.joinpath(image)
        # Otherwise, it is in the architecture's boot directory
        else:
            if not arch:
                die(f"Kernel image ('{image}') is in the arch/ directory but 'arch' was not provided!"
                    )
            kernel = kernel_location.joinpath("arch", arch, "boot", image)

    if not kernel.exists():
        die(f"Kernel ('{kernel}') does not exist!")

    return kernel


def green(string):
    """
    Prints string in bold green.

    Parameters:
        string (str): String to print in bold green.
    """
    print(f"\n\033[01;32m{string}\033[0m", flush=True)


def red(string):
    """
    Prints string in bold red.

    Parameters:
        string (str): String to print in bold red.
    """
    print(f"\n\033[01;31m{string}\033[0m", flush=True)
