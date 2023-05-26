#!/usr/bin/env python3

from argparse import ArgumentParser
import os
from pathlib import Path
import shutil
import subprocess

BUILDROOT_VERSION = '2022.02'
SUPPORTED_ARCHES = [
    'arm64',
    'arm64be',
    'arm',
    'm68k',
    'mips',
    'mipsel',
    'ppc32',
    'ppc64',
    'ppc64le',
    'riscv',
    's390',
    'x86',
    'x86_64',
]
ROOT_FOLDER = Path(__file__).resolve().parent
OUT_FOLDER = Path(ROOT_FOLDER, 'out')
SRC_FOLDER = Path(ROOT_FOLDER, 'src')


def buildroot_make(make_arg=None, **kwargs):
    make_cmd = ['make', f"-j{os.cpu_count()}"]
    if make_arg:
        make_cmd.append(make_arg)
    subprocess.run(make_cmd, **kwargs, check=True, cwd=SRC_FOLDER)


def build_image(architecture, edit_config):
    buildroot_make('clean')

    config = Path(ROOT_FOLDER, f"{architecture}.config")
    # Python documentation notes that when subprocess.Popen()'s env parameter
    # is not None, the current process's envirionment is not inherited, which
    # causes issues because PATH is not inherited. Add BR2_DEFCONFIG to the
    # environment, rather than replacing it (we support Python 3.8, so we
    # cannot use 'os.environ | {...}').
    buildroot_make('defconfig', env={**os.environ, 'BR2_DEFCONFIG': config})
    if edit_config:
        buildroot_make('menuconfig')
        buildroot_make('savedefconfig')

    buildroot_make()

    OUT_FOLDER.mkdir(exist_ok=True, parents=True)

    images = [Path(SRC_FOLDER, 'output/images/rootfs.cpio')]
    # For x86_64, we also build an ext4 image for UML
    if architecture == 'x86_64':
        images.append(images[0].with_suffix('.ext4'))

    for image in images:
        if not image.exists():
            raise FileNotFoundError(
                f"{image} could not be found! Did the build error?")
        zstd_cmd = [
            'zstd', '-f', '-19', '-o',
            Path(OUT_FOLDER, f"{architecture}-{image.name}.zst"), image
        ]
        subprocess.run(zstd_cmd, check=True)


def download_and_extract_buildroot():
    SRC_FOLDER.mkdir(parents=True)

    tarball = Path(ROOT_FOLDER, f"buildroot-{BUILDROOT_VERSION}.tar.gz")
    tarball.unlink(missing_ok=True)

    curl_cmd = [
        'curl', '-LSs', '-o', tarball,
        f"https://buildroot.org/downloads/{tarball.name}"
    ]
    subprocess.run(curl_cmd, check=True)

    sha256_cmd = ['sha256sum', '--quiet', '-c', f"{tarball.name}.sha256"]
    subprocess.run(sha256_cmd, check=True, cwd=ROOT_FOLDER)

    tar_cmd = [
        'tar', '-C', SRC_FOLDER, '--strip-components=1', '-axf', tarball
    ]
    subprocess.run(tar_cmd, check=True)

    tarball.unlink(missing_ok=True)


def download_buildroot_if_necessary():
    if SRC_FOLDER.exists():
        # Make support/scripts/setlocalversion do nothing because we are in a
        # git repository so it will return information about this repo, not
        # Buildroot
        setlocalversion = Path(SRC_FOLDER, 'support/scripts/setlocalversion')
        setlocalversion.write_text('', encoding='utf-8')

        installed_version = subprocess.run(['make', 'print-version'],
                                           capture_output=True,
                                           check=True,
                                           cwd=SRC_FOLDER,
                                           text=True).stdout.strip()
        if installed_version != BUILDROOT_VERSION:
            shutil.rmtree(SRC_FOLDER)
            download_and_extract_buildroot()
    else:
        download_and_extract_buildroot()


def parse_arguments():
    parser = ArgumentParser()

    parser.add_argument(
        '-a',
        '--architectures',
        choices=[*SUPPORTED_ARCHES, 'all'],
        default=SUPPORTED_ARCHES,
        help=
        'The architectures to build images for. Defaults to all supported architectures.',
        nargs='+')
    parser.add_argument(
        '-e',
        '--edit-config',
        action='store_true',
        help='Edit configuration file and run savedefconfig on result')

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    if not shutil.which('zstd'):
        raise RuntimeError(
            'zstd could not be found on your system, please install it!')

    architectures = SUPPORTED_ARCHES if 'all' in args.architectures else args.architectures

    download_buildroot_if_necessary()
    for arch in architectures:
        build_image(arch, args.edit_config)
