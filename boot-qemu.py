#!/usr/bin/env python3
# pylint: disable=invalid-name

from argparse import ArgumentParser
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

import utils

BOOT_UTILS = Path(__file__).resolve().parent
SUPPORTED_ARCHES = ['x86', 'x86_64']


class QEMURunner:

    def __init__(self):

        # Properties that can be adjusted by the user or class
        self.cmdline = []
        self.interactive = False
        self.kernel = None
        self.kernel_dir = None
        # It may be tempting to use self.use_kvm during initialization of
        # subclasses to set certain properties but the user can explicitly opt
        # out of KVM after instantiation, so any decisions based on it should
        # be confined to run().
        self.use_kvm = self._can_use_kvm()
        self.smp = 0
        self.timeout = ''

        self._default_kernel_path = None
        self._initrd_arch = None
        self._kvm_cpu = 'host'
        self._qemu_arch = None
        self._qemu_args = [
            '-display', 'none',
            '-nodefaults',
            '-no-reboot',
            '-serial', 'mon:stdio',
        ]  # yapf: disable
        self._qemu_path = None
        self._ram = '512m'

    def _can_use_kvm(self):
        return False

    def _get_default_smp_value(self):
        if not self.kernel_dir:
            raise RuntimeError('No kernel build folder specified?')

        # If kernel_dir is the kernel source, the configuration will be at
        # <kernel_dir>/.config
        #
        # If kernel_dir is the direct parent to the full kernel image, the
        # configuration could either be:
        #   * <kernel_dir>/.config (if the image is vmlinux)
        #   * <kernel_dir>/../../../.config (if the image is in arch/*/boot/)
        #   * <kernel_dir>/config (if the image is in a TuxMake folder)
        possible_locations = ['.config', '../../../.config', 'config']
        configuration = utils.find_first_file(self.kernel_dir,
                                              possible_locations,
                                              required=False)

        config_nr_cpus = 8  # sensible default based on treewide defaults,
        if configuration:
            conf_txt = configuration.read_text(encoding='utf-8')
            if (match := re.search(r'CONFIG_NR_CPUS=(\d+)', conf_txt)):
                config_nr_cpus = int(match.groups()[0])

        # Use the minimum of the number of usable processers for the script or
        # CONFIG_NR_CPUS.
        usable_cpus = os.cpu_count()
        return min(usable_cpus, config_nr_cpus)

    def _get_qemu_ver_string(self):
        if not self._qemu_path:
            raise RuntimeError('No path to QEMU set?')
        qemu_ver = subprocess.run([self._qemu_path, '--version'],
                                  capture_output=True,
                                  check=True,
                                  text=True)
        return qemu_ver.stdout.splitlines()[0]

    def _have_dev_kvm_access(self):
        return os.access('/dev/kvm', os.R_OK | os.W_OK)

    def _prepare_initrd(self):
        if not self._initrd_arch:
            raise RuntimeError('No initrd architecture specified?')
        if not (src := Path(BOOT_UTILS, 'images', self._initrd_arch,
                            'rootfs.cpio.zst')):
            raise FileNotFoundError(f"initrd ('{src}') does not exist?")

        (dst := src.with_suffix('')).unlink(missing_ok=True)

        utils.check_cmd('zstd')
        subprocess.run(['zstd', '-d', src, '-o', dst, '-q'], check=True)

        return dst

    def run(self):
        # Make sure QEMU binary is configured and available
        if not self._qemu_arch:
            raise RuntimeError('No QEMU architecture set?')
        qemu_bin = f"qemu-system-{self._qemu_arch}"
        if not (qemu_path := shutil.which(qemu_bin)):
            raise RuntimeError(
                f'{qemu_bin} could not be found on your system?')
        self._qemu_path = Path(qemu_path)

        # Locate kernel if it was not specified
        if self.kernel:
            if not self.kernel_dir:
                self.kernel_dir = self.kernel.parent
        else:
            if not self.kernel_dir:
                raise RuntimeError(
                    'No kernel image or kernel build folder specified?')
            if not self._default_kernel_path:
                raise RuntimeError('No default kernel path specified?')

            possible_kernel_locations = {
                Path(self.kernel_dir,
                     self._default_kernel_path),  # default (kbuild)
                Path(self.kernel_dir,
                     self._default_kernel_path.name),  # tuxmake
            }
            for loc in possible_kernel_locations:
                if loc.exists():
                    self.kernel = loc
                    break
            if not self.kernel:
                possible_locations = "', '".join(
                    str(path) for path in possible_kernel_locations)
                raise FileNotFoundError(
                    f"{self._default_kernel_path.name} could not be found at possible locations ('{possible_locations}')",
                )

        # Kernel options
        if self.interactive:
            self.cmdline.append('rdinit=/bin/sh')
        if self.cmdline:
            self._qemu_args += ['-append', ' '.join(self.cmdline)]
        self._qemu_args += ['-kernel', self.kernel]
        self._qemu_args += ['-initrd', self._prepare_initrd()]

        # KVM
        if self.use_kvm:
            if not self.smp:
                self.smp = self._get_default_smp_value()
            self._qemu_args += ['-cpu', self._kvm_cpu, '-enable-kvm']

        # Machine specs
        self._qemu_args += ['-m', self._ram]
        if self.smp:
            self._qemu_args += ['-smp', str(self.smp)]

        # Show information about QEMU
        utils.green(f"QEMU location: \033[0m{self._qemu_path.parent}")
        utils.green(f"QEMU version: \033[0m{self._get_qemu_ver_string()}")

        # Pretty print and run QEMU command
        qemu_cmd = []

        if not self.interactive:
            utils.check_cmd('timeout')
            qemu_cmd += ['timeout', '--foreground', self.timeout]

            utils.check_cmd('stdbuf')
            qemu_cmd += ['stdbuf', '-eL', '-oL']

        qemu_cmd += [qemu_path, *self._qemu_args]

        print(f"$ {' '.join(shlex.quote(str(elem)) for elem in qemu_cmd)}")
        try:
            subprocess.run(qemu_cmd, check=True)
        except subprocess.CalledProcessError as err:
            if err.returncode == 124:
                utils.red("ERROR: QEMU timed out!")
            else:
                utils.red("ERROR: QEMU did not exit cleanly!")
            sys.exit(err.returncode)


class X86QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline += ['console=ttyS0', 'earlycon=uart8250,io,0x3f8']

        self._default_kernel_path = Path('arch/x86/boot/bzImage')
        self._initrd_arch = 'x86'
        self._qemu_arch = 'i386'

    def _can_use_kvm(self):
        return platform.machine() == 'x86_64' and self._have_dev_kvm_access()

    def run(self):
        if self.use_kvm:
            self._qemu_args += ['-d', 'unimp,guest_errors']

        super().run()


class X8664QEMURunner(X86QEMURunner):

    def __init__(self):
        super().__init__()

        self._initrd_arch = self._qemu_arch = 'x86_64'

    def run(self):
        if not self.use_kvm:
            self._qemu_args += ['-cpu', 'Nehalem']

        super().run()


def parse_arguments():
    parser = ArgumentParser(description='Boot a Linux kernel in QEMU')

    parser.add_argument(
        '-a',
        '--architecture',
        choices=SUPPORTED_ARCHES,
        help='The architecture to boot. Possible values are: %(choices)s',
        metavar='ARCH',
        required=True)
    parser.add_argument(
        '-k',
        '--kernel-location',
        required=True,
        help='Absolute or relative path to kernel image or build folder.')
    parser.add_argument('--append',
                        help='Append items to kernel cmdline',
                        nargs='+')
    parser.add_argument(
        '--no-kvm',
        action='store_true',
        help='Do not use KVM for accelration even when supported.')
    parser.add_argument(
        '-i',
        '--interactive',
        '--shell',
        action='store_true',
        help='Instead of immediately shutting down machine, spawn a shell.')
    parser.add_argument('-t',
                        '--timeout',
                        default='3m',
                        help="Value to pass to 'timeout' (default: '3m')")

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    arch_to_runner = {
        'x86': X86QEMURunner,
        'x86_64': X8664QEMURunner,
    }
    runner = arch_to_runner[args.architecture]()

    if not (kernel_location := Path(args.kernel_location).resolve()).exists():
        raise FileNotFoundError(
            f"Supplied kernel location ('{kernel_location}') does not exist!")
    if kernel_location.is_file():
        runner.kernel = kernel_location
    else:
        runner.kernel_dir = kernel_location

    if args.append:
        runner.cmdline += args.append

    if args.no_kvm:
        runner.use_kvm = False

    runner.interactive = args.interactive
    runner.timeout = args.timeout

    runner.run()
