#!/usr/bin/env python3

import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

BOOT_UTILS = Path(__file__).resolve().parent
REPO = 'ClangBuiltLinux/boot-utils'


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


def download_initrd(gh_json, local_dest):
    """
    Download an initial ramdisk from a GitHub release

    Parameters:
        gh_json (dict): A serialized JSON object from a repo's release endpoint
        local_dest (Path): A Path object pointing to the local file destination
    """
    assets = gh_json['assets']
    tag = gh_json['tag_name']
    url = gh_json['url']

    # Turns '<arch>/rootfs.<format>.zst' into '<arch>-rootfs.<format>.zst'
    remote_file = '-'.join(local_dest.parts[-2:])

    for asset in assets:
        if asset['name'] == remote_file:
            curl_cmd = [
                'curl', '-LSs', '-o', local_dest, asset['browser_download_url']
            ]
            subprocess.run(curl_cmd, check=True)

            # Update the '.release' file in the same folder as the download
            local_dest.with_name('.release').write_text(tag, encoding='utf-8')

            return

    raise RuntimeError(f"Failed to find {remote_file} in downloads of {url}?")


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


def get_gh_json(endpoint):
    """
    Query a GitHub API endpoint.

    Parameters:
        endpoint (str): The URL of the endpoint to query.

    Returns:
        A JSON object from the result of the query.
    """
    curl_cmd = ['curl', '-LSs']
    if 'GITHUB_TOKEN' in os.environ:
        # https://docs.github.com/en/rest/overview/authenticating-to-the-rest-api
        curl_cmd += [
            '-H',
            'Accept: application/vnd.github+json',
            '-H',
            f"Authorization: Bearer {os.environ['GITHUB_TOKEN']}",
        ]
    curl_cmd.append(endpoint)

    try:
        curl_out = subprocess.run(curl_cmd,
                                  capture_output=True,
                                  check=True,
                                  text=True).stdout
    except subprocess.CalledProcessError as err:
        raise RuntimeError(
            f"Failed to query GitHub API at {endpoint}: {err.stderr}") from err

    return json.loads(curl_out)


def green(string):
    """
    Prints string in bold green.

    Parameters:
        string (str): String to print in bold green.
    """
    print(f"\n\033[01;32m{string}\033[0m", flush=True)


def prepare_initrd(architecture, rootfs_format='cpio', gh_json_file=None):
    """
    Returns a decompressed initial ramdisk.

    Parameters:
        architecture (str): Architecture to download image for.
        rootfs_format (str): Initrd format ('cpio' or 'ext4')
    """
    src = Path(BOOT_UTILS, 'images', architecture,
               f"rootfs.{rootfs_format}.zst")
    src.parent.mkdir(exist_ok=True, parents=True)

    # If the user supplied a GitHub release JSON file, we do not need to bother
    # querying the GitHub API at all.
    if gh_json_file:
        if not gh_json_file.exists():
            raise FileNotFoundError(
                f"Provided GitHub JSON file ('{gh_json_file}') does not exist!"
            )
        gh_json_rel = json.loads(gh_json_file.read_text(encoding='utf-8'))
    else:
        # Make sure that the current user is not rate limited by GitHub,
        # otherwise the next API call will not return valid information.
        gh_json_rl = get_gh_json('https://api.github.com/rate_limit')

        # If we have API calls remaining or have already queried the API previously
        # and cached the result, we can query for the latest release to make sure
        # that we are up to date.
        if (remaining := gh_json_rl['resources']['core']['remaining']) > 0:
            gh_json_rel = get_gh_json(
                f"https://api.github.com/repos/{REPO}/releases/latest")
        elif not src.exists():
            limit = gh_json_rl['resources']['core']['limit']
            raise RuntimeError(
                f"Cannot query GitHub API for latest images release due to rate limit (remaining: {remaining}, limit: {limit}) and {src} does not exist already! "
                'Download it manually or supply a GitHub personal access token via the GITHUB_TOKEN environment variable to make an authenticated GitHub API request.'
            )

    # Download the ramdisk if it is not already downloaded
    if not src.exists():
        # gh_json_rel cannot be unset when used here because the elif condition
        # above is the same as this one, which causes the script to exit.
        # pylint: disable-next=possibly-used-before-assignment
        download_initrd(gh_json_rel, src)
    # If it is already downloaded, check that it is up to date and download
    # an update only if necessary.
    elif (rel_file := src.with_name('.release')).exists():
        cur_rel = rel_file.read_text(encoding='utf-8')
        supplied_rel = gh_json_rel['tag_name']
        if cur_rel != supplied_rel:
            download_initrd(gh_json_rel, src)

    check_cmd('zstd')
    (dst := src.with_suffix('')).unlink(missing_ok=True)
    subprocess.run(['zstd', '-d', src, '-o', dst, '-q'], check=True)

    return dst


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
