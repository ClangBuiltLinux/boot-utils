#!/usr/bin/env python3

from argparse import ArgumentParser
import datetime
import os
from pathlib import Path
import shutil
import subprocess

BUILDROOT_VERSION = '2023.02.2'
SUPPORTED_ARCHES = [
    'arm64',
    'arm64be',
    'arm',
    'loongarch',
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
RELEASE_TAG = datetime.datetime.now(
    tz=datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
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
    if SRC_FOLDER.exists():
        shutil.rmtree(SRC_FOLDER)
    SRC_FOLDER.mkdir(parents=True)

    tarball = Path(ROOT_FOLDER, f"buildroot-{BUILDROOT_VERSION}.tar.gz")
    if not tarball.exists():
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

    if (patches := list(ROOT_FOLDER.glob('*.patch'))):
        for patch in patches:
            patch_cmd = [
                'patch', '--directory', SRC_FOLDER, '--input', patch,
                '--strip', '1'
            ]
            try:
                subprocess.run(patch_cmd, check=True)
            except subprocess.CalledProcessError as err:
                raise RuntimeError(
                    f"{patch} did not apply to Buildroot {BUILDROOT_VERSION}, does it need to be updated?"
                ) from err


def release_images():
    if not shutil.which('gh'):
        raise RuntimeError(
            "Could not find GitHub CLI ('gh') on your system, please install it to do releases!"
        )

    gh_cmd = [
        'gh', '-R', 'ClangBuiltLinux/boot-utils', 'release', 'create',
        '--generate-notes', RELEASE_TAG, *list(OUT_FOLDER.iterdir())
    ]
    subprocess.run(gh_cmd, check=True)


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
    parser.add_argument(
        '-r',
        '--release',
        action='store_true',
        help=f"Create a release on GitHub (tag: {RELEASE_TAG})")

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    if not shutil.which('zstd'):
        raise RuntimeError(
            'zstd could not be found on your system, please install it!')

    architectures = SUPPORTED_ARCHES if 'all' in args.architectures else args.architectures

    download_and_extract_buildroot()
    for arch in architectures:
        build_image(arch, args.edit_config)

    if args.release:
        release_images()
