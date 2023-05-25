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


def find_first_file(relative_root, possible_files, required=True):
    """
    Attempts to find the first option available in the list of files relative
    to a specified root folder.

    Parameters:
        relative_root (Path): A Path object containing the folder to search for
                              files within.
        possible_files (list): A list of Paths that may be within the relative
                               root folder. They will be automatically appended
                               to relative_root.
        required (bool): Whether or not the file is required, which determines
                         if not finding the file is an error.
    Returns:
        The full path to the first file found in the list. If none could be
        found, an Exception is raised.
    """
    for possible_file in possible_files:
        if (full_path := relative_root.joinpath(possible_file)).exists():
            return full_path
    if required:
        files_str = "', '".join([str(elem) for elem in possible_files])
        raise FileNotFoundError(
            f"No files from list ('{files_str}') could be found within '{relative_root}'!",
        )
    return None


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
    # If the image is an uncompressed vmlinux or a UML image, it is in the
    # root of the build folder
    elif image in ("vmlinux", "linux"):
        kernel = kernel_location.joinpath(image)
    # Otherwise, it is in the architecture's boot directory
    else:
        if not arch:
            die(f"Kernel image ('{image}') is in the arch/ directory but 'arch' was not provided!"
                )
        kernel = kernel_location.joinpath("arch", arch, "boot", image)

    if not kernel.exists():
        die(f"Kernel ('{kernel}') does not exist!")

    return kernel.resolve()


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


def yellow(string):
    """
    Prints string in bold yellow.

    Parameters:
        string (str): String to print in bold yellow.
    """
    print(f"\n\033[01;33m{string}\033[0m", flush=True)
