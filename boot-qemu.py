#!/usr/bin/env python3
# pylint: disable=invalid-name

from argparse import ArgumentParser
import contextlib
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys

import utils

SUPPORTED_ARCHES = [
    'arm',
    'arm32_v5',
    'arm32_v6',
    'arm32_v7',
    'arm64',
    'arm64be',
    'm68k',
    'mips',
    'mipsel',
    'ppc32',
    'ppc32_mac',
    'ppc64',
    'ppc64le',
    'riscv',
    's390',
    'x86',
    'x86_64',
]


class QEMURunner:

    def __init__(self):

        # Properties that can be adjusted by the user or class
        self.cmdline = []
        self.efi = False
        self.gdb = False
        self.gdb_bin = ''
        self.interactive = False
        self.kernel = None
        self.kernel_dir = None
        self.supports_efi = False
        # It may be tempting to use self.use_kvm during initialization of
        # subclasses to set certain properties but the user can explicitly opt
        # out of KVM after instantiation, so any decisions based on it should
        # be confined to run().
        self.use_kvm = False
        self.smp = 0
        self.timeout = ''

        self._default_kernel_path = None
        self._dtb = None
        self._efi_img = None
        self._efi_vars = None
        self._initrd_arch = None
        self._kvm_cpu = ['host']
        self._qemu_arch = None
        self._qemu_args = [
            '-display', 'none',
            '-nodefaults',
        ]  # yapf: disable
        self._qemu_path = None
        self._ram = '512m'

    def _find_dtb(self):
        if not self._dtb:
            raise RuntimeError('No dtb set?')
        if not self.kernel:
            raise RuntimeError('Cannot locate dtb without kernel')

        # If we are in a boot folder, look for them in the dts folder in it.
        # Otherwise, assume there is a 'dtbs' folder in the same folder as the
        # kernel image (tuxmake)
        dtb_dir = 'dts' if self.kernel.parent.name == 'boot' else 'dtbs'
        if not (dtb := Path(self.kernel.parent, dtb_dir, self._dtb)).exists():
            raise FileNotFoundError(
                f"dtb ('{self._dtb}') is required for booting but it could not be found at expected location ('{dtb}')",
            )

        return dtb

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

    def _get_kernel_ver_tuple(self, decomp_prog):
        if not self.kernel:
            raise RuntimeError('No kernel set?')

        utils.check_cmd(decomp_prog)
        if decomp_prog in ('gzip', ):
            decomp_cmd = [decomp_prog, '-c', '-d', self.kernel]
        decomp = subprocess.run(decomp_cmd, capture_output=True, check=True)

        utils.check_cmd('strings')
        strings = subprocess.run('strings',
                                 capture_output=True,
                                 check=True,
                                 input=decomp.stdout)
        strings_stdout = strings.stdout.decode(encoding='utf-8',
                                               errors='ignore')

        if not (match := re.search(r'^Linux version (\d+\.\d+\.\d+)',
                                   strings_stdout,
                                   flags=re.M)):
            raise RuntimeError(
                f"Could not find Linux version in {self.kernel}?")

        return tuple(int(x) for x in match.groups()[0].split('.'))

    def _get_qemu_ver_string(self):
        if not self._qemu_path:
            raise RuntimeError('No path to QEMU set?')
        qemu_ver = subprocess.run([self._qemu_path, '--version'],
                                  capture_output=True,
                                  check=True,
                                  text=True)
        return qemu_ver.stdout.splitlines()[0]

    def _get_qemu_ver_tuple(self):
        qemu_ver_string = self._get_qemu_ver_string()
        if not (match := re.search(r'version (\d+\.\d+.\d+)',
                                   qemu_ver_string)):
            raise RuntimeError('Could not find QEMU version?')
        return tuple(int(x) for x in match.groups()[0].split('.'))

    def _have_dev_kvm_access(self):
        return os.access('/dev/kvm', os.R_OK | os.W_OK)

    def _prepare_initrd(self):
        if not self._initrd_arch:
            raise RuntimeError('No initrd architecture specified?')
        return utils.prepare_initrd(self._initrd_arch)

    def _run_fg(self):
        # Pretty print and run QEMU command
        qemu_cmd = []

        if not self.interactive:
            utils.check_cmd('timeout')
            qemu_cmd += ['timeout', '--foreground', self.timeout]

            utils.check_cmd('stdbuf')
            qemu_cmd += ['stdbuf', '-eL', '-oL']

        qemu_cmd += [self._qemu_path, *self._qemu_args]

        print(f"$ {' '.join(shlex.quote(str(elem)) for elem in qemu_cmd)}")
        try:
            subprocess.run(qemu_cmd, check=True)
        except subprocess.CalledProcessError as err:
            if err.returncode == 124:
                utils.red("ERROR: QEMU timed out!")
            else:
                utils.red("ERROR: QEMU did not exit cleanly!")
            sys.exit(err.returncode)

    def _run_gdb(self):
        qemu_cmd = [self._qemu_path, *self._qemu_args]

        utils.check_cmd(self.gdb_bin)
        utils.check_cmd('lsof')

        gdb_cmd = [
            self.gdb_bin,
            Path(self.kernel_dir, 'vmlinux'),
            '-ex',
            'target remote :1234',
        ]

        while True:
            lsof = subprocess.run(['lsof', '-i:1234'],
                                  capture_output=True,
                                  check=False)
            if lsof.returncode == 0:
                utils.die('Port 1234 is already in use, is QEMU running?')

            utils.green('Starting QEMU with gdb connection on port 1234...')
            with subprocess.Popen(qemu_cmd,
                                  preexec_fn=os.setpgrp) as qemu_proc:
                utils.green(f"Starting {self.gdb_bin}...")
                with subprocess.Popen(gdb_cmd) as gdb_proc, \
                     contextlib.suppress(KeyboardInterrupt):
                    gdb_proc.wait()

                utils.red('Killing QEMU...')
                qemu_proc.kill()

            answer = input('Re-run QEMU + gdb [y/n] ')
            if answer.lower() == 'n':
                break

    def _set_kernel_vars(self):
        if self.kernel:
            if not self.kernel_dir:
                self.kernel_dir = self.kernel.parent
            # Nothing else to do, kernel image and build folder located and set
            return

        if not self.kernel_dir:
            raise RuntimeError(
                'No kernel image or kernel build folder specified?')
        if not self._default_kernel_path:
            raise RuntimeError('No default kernel path specified?')

        possible_kernel_locations = {
            Path(self._default_kernel_path),  # default (kbuild)
            Path(self._default_kernel_path.name),  # tuxmake
        }
        self.kernel = utils.find_first_file(self.kernel_dir,
                                            possible_kernel_locations)

    def _set_qemu_path(self):
        if self._qemu_path:
            return  # already found and set
        if not self._qemu_arch:
            raise RuntimeError('No QEMU architecture set?')
        qemu_bin = f"qemu-system-{self._qemu_arch}"
        if not (qemu_path := shutil.which(qemu_bin)):
            raise RuntimeError(
                f'{qemu_bin} could not be found on your system?')
        self._qemu_path = Path(qemu_path)

    def run(self):
        # Make sure QEMU binary is configured and available
        self._set_qemu_path()

        # Locate kernel (may be done earlier in subclasses)
        self._set_kernel_vars()

        # EFI:
        if self.efi:
            self._qemu_args += [
                '-drive', f"if=pflash,format=raw,file={self._efi_img},readonly=on",
                '-drive', f"if=pflash,format=raw,file={self._efi_vars}",
                '-object', 'rng-random,filename=/dev/urandom,id=rng0',
                '-device', 'virtio-rng-pci',
            ]  # yapf: disable

        # Kernel options
        if self.interactive or args.gdb:
            self.cmdline.append('rdinit=/bin/sh')
        if self.gdb:
            self.cmdline.append('nokaslr')
        if self.cmdline:
            self._qemu_args += ['-append', ' '.join(self.cmdline)]
        if self._dtb:
            self._qemu_args += ['-dtb', self._find_dtb()]
        self._qemu_args += ['-kernel', self.kernel]
        self._qemu_args += ['-initrd', self._prepare_initrd()]

        # KVM
        if self.use_kvm:
            if not self.smp:
                self.smp = self._get_default_smp_value()
            self._qemu_args += ['-cpu', ','.join(self._kvm_cpu), '-enable-kvm']

        # Machine specs
        self._qemu_args += ['-m', self._ram]
        if self.smp:
            self._qemu_args += ['-smp', str(self.smp)]

        # Show information about QEMU
        utils.green(f"QEMU location: \033[0m{self._qemu_path.parent}")
        utils.green(f"QEMU version: \033[0m{self._get_qemu_ver_string()}")

        if self.gdb:
            self._qemu_args += ['-s', '-S']

            self._run_gdb()
        else:
            self._qemu_args += ['-serial', 'mon:stdio']

            self._run_fg()


class ARMQEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('arch/arm/boot/zImage')
        self._initrd_arch = self._qemu_arch = 'arm'
        self._machine = 'virt'
        self._qemu_args.append('-no-reboot')

    def run(self):
        self._qemu_args += ['-machine', self._machine]

        super().run()


class ARMV5QEMURunner(ARMQEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline.append('earlycon')

        self._dtb = 'aspeed-bmc-opp-palmetto.dtb'
        self._machine = 'palmetto-bmc'


class ARMV6QEMURunner(ARMQEMURunner):

    def __init__(self):
        super().__init__()

        self._dtb = 'aspeed-bmc-opp-romulus.dtb'
        self._machine = 'romulus-bmc'


class ARMV7QEMURunner(ARMQEMURunner):

    def __init__(self):
        super().__init__()

        self.use_kvm = self._can_use_kvm()

        self.cmdline += ['console=ttyAMA0', 'earlycon']

    def _can_use_kvm(self):
        # 32-bit ARM KVM was ripped out in 5.7, so we do not bother checking
        # for it here.
        if platform.machine() != 'aarch64':
            return False

        # 32-bit EL1 is not supported on all cores so support for it must be
        # explicitly queried via the KVM_CHECK_EXTENSION ioctl().
        try:
            subprocess.run(Path(utils.BOOT_UTILS, 'utils',
                                'aarch64_32_bit_el1_supported'),
                           check=True)
        except subprocess.CalledProcessError:
            return False

        return self._have_dev_kvm_access()

    def run(self):
        if self.use_kvm:
            self._kvm_cpu.append('aarch64=off')
            self._qemu_arch = 'aarch64'

        super().run()


class ARM64QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline += ['console=ttyAMA0', 'earlycon']
        self.supports_efi = True
        self.use_kvm = platform.machine() == 'aarch64' and \
                       self._have_dev_kvm_access()

        self._default_kernel_path = Path('arch/arm64/boot/Image.gz')
        self._initrd_arch = 'arm64'
        self._qemu_arch = 'aarch64'

    def _get_cpu_val(self):
        cpu = ['max']

        self._set_qemu_path()
        # See the two gitlab links below for more details
        if (qemu_ver := self._get_qemu_ver_tuple()) >= (6, 2, 50):
            self._set_kernel_vars()
            kernel_ver = self._get_kernel_ver_tuple('gzip')

            # https://gitlab.com/qemu-project/qemu/-/issues/964
            if kernel_ver < (4, 16, 0):
                cpu = ['cortex-a72']
            # https://gitlab.com/qemu-project/qemu/-/commit/69b2265d5fe8e0f401d75e175e0a243a7d505e53
            elif kernel_ver < (5, 12, 0):
                cpu.append('lpa2=off')

        # https://lore.kernel.org/YlgVa+AP0g4IYvzN@lakrids/
        if 'max' in cpu and qemu_ver >= (6, 0, 0):
            cpu.append('pauth-impdef=true')

        return cpu

    def _setup_efi(self):
        # Sizing the images to 64M is recommended by "Prepare the firmware" section at
        # https://mirrors.edge.kernel.org/pub/linux/kernel/people/will/docs/qemu/qemu-arm64-howto.html
        efi_img_size = 64 * 1024 * 1024  # 64M

        usr_share = Path('/usr/share')

        aavmf_locations = [
            Path('edk2/aarch64/QEMU_EFI.silent.fd'),  # Fedora
            Path('edk2/aarch64/QEMU_EFI.fd'),  # Arch Linux (current)
            Path('edk2-armvirt/aarch64/QEMU_EFI.fd'),  # Arch Linux (old)
            Path('qemu-efi-aarch64/QEMU_EFI.fd'),  # Debian and Ubuntu
        ]
        aavmf = utils.find_first_file(usr_share, aavmf_locations)

        self._efi_img = Path(utils.BOOT_UTILS, 'images', self._initrd_arch,
                             'efi.img')
        # This file is in /usr/share, so it must be copied in order to be
        # modified.
        shutil.copyfile(aavmf, self._efi_img)
        with self._efi_img.open(mode='r+b') as file:
            file.truncate(efi_img_size)

        self._efi_vars = self._efi_img.with_name('efivars.img')
        self._efi_vars.unlink(missing_ok=True)
        with self._efi_vars.open(mode='xb') as file:
            file.truncate(efi_img_size)

    def run(self):
        machine = ['virt', 'gic-version=max']

        if not self.use_kvm:
            cpu_val = self._get_cpu_val()
            self._qemu_args += ['-cpu', ','.join(cpu_val)]

            # Boot with VHE emulation, which allows the kernel to run at EL2.
            # KVM does not emulate VHE, so this cannot be unconditional.
            machine.append('virtualization=true')

        self._qemu_args += ['-machine', ','.join(machine)]

        if self.efi:
            self._setup_efi()

        super().run()


class ARM64BEQEMURunner(ARM64QEMURunner):

    def __init__(self):
        super().__init__()

        self.supports_efi = False

        self._initrd_arch = 'arm64be'


class M68KQEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline.append('console=ttyS0,115200')
        self._default_kernel_path = Path('vmlinux')
        self._initrd_arch = self._qemu_arch = 'm68k'
        self._qemu_args += [
            '-cpu', 'm68040',
            '-M', 'q800',
            '-no-reboot',
        ]  # yapf: disable


class MIPSQEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('vmlinux')
        self._initrd_arch = self._qemu_arch = 'mips'
        self._qemu_args += ['-cpu', '24Kf', '-machine', 'malta']


class MIPSELQEMURunner(MIPSQEMURunner):

    def __init__(self):
        super().__init__()

        self._initrd_arch = self._qemu_arch = 'mipsel'


class PowerPC32QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline.append('console=ttyS0')
        self._default_kernel_path = Path('arch/powerpc/boot/uImage')
        self._initrd_arch = 'ppc32'
        self._machine = 'bamboo'
        self._qemu_arch = 'ppc'
        self._qemu_args.append('-no-reboot')
        self._ram = '128m'

    def run(self):
        self._qemu_args += ['-machine', self._machine]

        super().run()


class PowerPC32MacQEMURunner(PowerPC32QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('vmlinux')
        self._machine = 'mac99'


class PowerPC64QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('vmlinux')
        self._initrd_arch = self._qemu_arch = 'ppc64'
        self._qemu_args += [
            '-cpu', 'power8',
            '-machine', 'pseries',
            '-vga', 'none',
        ]  # yapf: disable
        self._ram = '1G'


class PowerPC64LEQEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('arch/powerpc/boot/zImage.epapr')
        self._initrd_arch = 'ppc64le'
        self._qemu_arch = 'ppc64'
        self._qemu_args += [
            '-device', 'ipmi-bmc-sim,id=bmc0',
            '-device', 'isa-ipmi-bt,bmc=bmc0,irq=10',
            '-machine', 'powernv',
        ]  # yapf: disable
        self._ram = '2G'


class RISCVQEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline.append('earlycon')
        self._default_kernel_path = Path('arch/riscv/boot/Image')
        self._initrd_arch = 'riscv'
        self._qemu_arch = 'riscv64'

        deb_bios = '/usr/lib/riscv64-linux-gnu/opensbi/qemu/virt/fw_jump.elf'
        if 'BIOS' in os.environ:
            bios = os.environ['BIOS']
        elif Path(deb_bios).exists():
            bios = deb_bios
        else:
            bios = 'default'
        self._qemu_args += ['-bios', bios, '-M', 'virt']


class S390QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self._default_kernel_path = Path('arch/s390/boot/bzImage')
        self._initrd_arch = 's390'
        self._qemu_arch = 's390x'
        self._qemu_args += ['-M', 's390-ccw-virtio']


class X86QEMURunner(QEMURunner):

    def __init__(self):
        super().__init__()

        self.cmdline += ['console=ttyS0', 'earlycon=uart8250,io,0x3f8']
        self.use_kvm = platform.machine() == 'x86_64' and \
                       self._have_dev_kvm_access()

        self._default_kernel_path = Path('arch/x86/boot/bzImage')
        self._initrd_arch = 'x86'
        self._qemu_arch = 'i386'

    def run(self):
        if self.use_kvm and not self.efi:
            # There are a lot of messages along the line of
            # "Invalid read at addr 0xFED40000, size 1, region '(null)', reason: rejected"
            # with EFI, so do not bother.
            self._qemu_args += ['-d', 'unimp,guest_errors']

        super().run()


class X8664QEMURunner(X86QEMURunner):

    def __init__(self):
        super().__init__()

        self.supports_efi = True

        self._initrd_arch = self._qemu_arch = 'x86_64'

    def run(self):
        if not self.use_kvm:
            self._qemu_args += ['-cpu', 'Nehalem']

        if self.efi:
            usr_share = Path('/usr/share')
            ovmf_locations = [
                Path('edk2/x64/OVMF_CODE.fd'),  # Arch Linux (current), Fedora
                Path('edk2-ovmf/x64/OVMF_CODE.fd'),  # Arch Linux (old)
                Path('OVMF/OVMF_CODE.fd'),  # Debian and Ubuntu
            ]
            self._efi_img = utils.find_first_file(usr_share, ovmf_locations)

            ovmf_vars_locations = [
                Path('edk2/x64/OVMF_VARS.fd'),  # Arch Linux and Fedora
                Path('OVMF/OVMF_VARS.fd'),  # Debian and Ubuntu
            ]
            ovmf_vars = utils.find_first_file(usr_share, ovmf_vars_locations)
            self._efi_vars = Path(utils.BOOT_UTILS, 'images', self.initrd_arch,
                                  ovmf_vars.name)
            # This file is in /usr/share, so it must be copied in order to be
            # modified.
            shutil.copyfile(ovmf_vars, self._efi_vars)

        super().run()


def guess_arch(kernel_arg):
    # kernel_arg is either a path to the kernel build folder or a full kernel
    # location. If it is a file, we need to strip off the basename so that we
    # can easily navigate around with '..'.
    if (kernel_dir := kernel_arg).is_file():
        kernel_dir = kernel_dir.parent

    # If kernel_location is the kernel build folder, vmlinux will be at
    # <kernel_dir>/vmlinux
    #
    # If kernel_location is a full kernel location, it could either be:
    #   * <kernel_dir>/vmlinux (if the image is vmlinux)
    #   * <kernel_dir>/../../../vmlinux (if the image is in arch/*/boot/)
    #
    # Note: 'required=False' just to provide our own exception.
    vmlinux_locations = ['vmlinux', '../../../vmlinux']
    if not (vmlinux := utils.find_first_file(
            kernel_dir, vmlinux_locations, required=False)):
        raise RuntimeError(
            'Architecture was not provided and vmlinux could not be found!')

    if not (file := shutil.which('file')):
        raise RuntimeError(
            "Architecture was not provided and 'file' is not installed!")

    # Get output of file
    file_out = subprocess.run([file, vmlinux],
                              capture_output=True,
                              check=True,
                              text=True).stdout.strip()

    # Unfortunately, 'file' is not terribly precise when it comes to
    # microarchitecture or architecture revisions. As such, there are certain
    # strings that are just ambiguous so we bail out and let the user tell us
    # exactly what architecture they were hoping to boot.
    file_rosetta = {
        'ELF 32-bit LSB executable, ARM, EABI5': 'ambiguous',  # could be any arm32
        'ELF 64-bit LSB executable, ARM aarch64': 'arm64',
        'ELF 64-bit MSB executable, ARM aarch64': 'arm64be',
        'ELF 64-bit LSB pie executable, ARM aarch64': 'arm64',
        'ELF 64-bit MSB pie executable, ARM aarch64': 'arm64be',
        'ELF 32-bit MSB executable, Motorola m68k, 68020': 'm68k',
        'ELF 32-bit MSB executable, MIPS, MIPS32': 'mips',
        'ELF 32-bit LSB executable, MIPS, MIPS32': 'mipsel',
        'ELF 32-bit MSB executable, PowerPC': 'ambiguous',  # could be ppc32 or ppc32_mac
        'ELF 64-bit MSB executable, 64-bit PowerPC or cisco 7500, Power ELF V1 ABI': 'ppc64',
        'ELF 64-bit MSB executable, 64-bit PowerPC or cisco 7500, OpenPOWER ELF V2 ABI': 'ppc64',
        'ELF 64-bit MSB pie executable, 64-bit PowerPC or cisco 7500, OpenPOWER ELF V2 ABI': 'ppc64',
        'ELF 64-bit LSB executable, 64-bit PowerPC or cisco 7500, OpenPOWER ELF V2 ABI': 'ppc64le',
        'ELF 64-bit LSB executable, UCB RISC-V': 'riscv',
        'ELF 64-bit MSB executable, IBM S/390': 's390',
        'ELF 32-bit LSB executable, Intel 80386': 'x86',
        'ELF 64-bit LSB executable, x86-64': 'x86_64',
    }  # yapf: disable
    for string, value in file_rosetta.items():
        if string in file_out:
            if value == 'ambiguous':
                raise RuntimeError(
                    f"'{string}' found in '{file_out}' but the architecture is ambiguous, please explicitly specify it via '-a'!"
                )
            return value

    raise RuntimeError(
        f"Architecture could not be deduced from '{file_out}', please explicitly specify it via '-a' or add support for it to guess_arch()!"
    )


def parse_arguments():
    parser = ArgumentParser(description='Boot a Linux kernel in QEMU')

    parser.add_argument(
        '-a',
        '--architecture',
        choices=SUPPORTED_ARCHES,
        help=
        "The architecture to boot. If omitted, value will be guessed based on 'vmlinux' if available. Possible values are: %(choices)s",
        metavar='ARCH')
    parser.add_argument('--efi',
                        action='store_true',
                        help='Boot kernel via UEFI (x86_64 only)')
    parser.add_argument(
        '-g',
        '--gdb',
        action='store_true',
        help="Start QEMU with '-s -S' then launch gdb on 'vmlinux'")
    parser.add_argument(
        '--gdb-bin',
        default='gdb-multiarch',
        help='gdb binary to use for debugging (default: gdb-multiarch)')
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
    parser.add_argument(
        '-s',
        '--smp',
        type=int,
        help=
        'Number of processors for virtual machine (default: only KVM machines will use multiple vCPUs.)',
    )
    parser.add_argument('-t',
                        '--timeout',
                        default='3m',
                        help="Value to pass to 'timeout' (default: '3m')")

    return parser.parse_args()


if __name__ == '__main__':
    args = parse_arguments()

    if not (kernel_location := Path(args.kernel_location).resolve()).exists():
        raise FileNotFoundError(
            f"Supplied kernel location ('{kernel_location}') does not exist!")

    if not (arch := args.architecture):
        arch = guess_arch(kernel_location)

    arch_to_runner = {
        'arm': ARMV7QEMURunner,
        'arm32_v5': ARMV5QEMURunner,
        'arm32_v6': ARMV6QEMURunner,
        'arm32_v7': ARMV7QEMURunner,
        'arm64': ARM64QEMURunner,
        'arm64be': ARM64BEQEMURunner,
        'm68k': M68KQEMURunner,
        'mips': MIPSQEMURunner,
        'mipsel': MIPSELQEMURunner,
        'ppc32': PowerPC32QEMURunner,
        'ppc32_mac': PowerPC32MacQEMURunner,
        'ppc64': PowerPC64QEMURunner,
        'ppc64le': PowerPC64LEQEMURunner,
        'riscv': RISCVQEMURunner,
        's390': S390QEMURunner,
        'x86': X86QEMURunner,
        'x86_64': X8664QEMURunner,
    }
    runner = arch_to_runner[arch]()

    if kernel_location.is_file():
        if args.gdb and kernel_location.name != 'vmlinux':
            raise RuntimeError(
                'Debugging with gdb requires a kernel build folder to locate vmlinux',
            )
        runner.kernel = kernel_location
    else:
        runner.kernel_dir = kernel_location

    if args.append:
        runner.cmdline += args.append

    if args.efi:
        runner.efi = runner.supports_efi
        if not runner.efi:
            utils.yellow(
                f"EFI boot requested on unsupported architecture ('{arch}'), ignoring..."
            )

    if args.gdb:
        runner.gdb = True
        runner.gdb_bin = args.gdb_bin

    if args.no_kvm:
        runner.use_kvm = False

    if args.smp:
        runner.smp = args.smp

    runner.interactive = args.interactive
    runner.timeout = args.timeout

    runner.run()
