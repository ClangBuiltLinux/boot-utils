#!/usr/bin/env python3
# pylint: disable=invalid-name

import argparse
import contextlib
import grp
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

import utils

base_folder = Path(__file__).resolve().parent
shared_folder = base_folder.joinpath('shared')
supported_architectures = [
    "arm", "arm32_v5", "arm32_v6", "arm32_v7", "arm64", "arm64be", "m68k",
    "mips", "mipsel", "ppc32", "ppc32_mac", "ppc64", "ppc64le", "riscv",
    "s390", "x86", "x86_64"
]


def parse_arguments():
    """
    Parses arguments to script.

    Returns:
        A Namespace object containing key values from parser.parse_args()
    """
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "-a",
        "--architecture",
        metavar="ARCH",
        required=True,
        type=str,
        choices=supported_architectures,
        help="The architecture to boot. Possible values are: %(choices)s")
    parser.add_argument(
        "--append",
        default="",
        type=str,
        help="A string of values to pass to the kernel command line.")
    parser.add_argument("--efi",
                        action="store_true",
                        help="Boot kernel using UEFI (arm64 and x86_64 only).")
    parser.add_argument(
        "-g",
        "--gdb",
        action="store_true",
        help="Start QEMU with '-s -S' then launch GDB on 'vmlinux'.")
    parser.add_argument("--gdb-bin",
                        type=str,
                        default="gdb-multiarch",
                        help="GDB binary to use for debugging.")
    parser.add_argument(
        "-i",
        "--interactive",
        "--shell",
        action="store_true",
        help=  # noqa: E251
        "Instead of immediately shutting down the machine upon successful boot, pass 'rdinit=/bin/sh' on the kernel command line to allow interacting with the machine via a shell."
    )
    parser.add_argument(
        "-k",
        "--kernel-location",
        required=True,
        type=str,
        help=  # noqa: E251
        "Path to kernel image or kernel build folder to search for image in. Can be an absolute or relative path."
    )
    parser.add_argument(
        "--no-kvm",
        action="store_true",
        help=  # noqa: E251
        "Do not use KVM for acceleration even when supported (only recommended for debugging)."
    )
    parser.add_argument(
        "-s",
        "--smp",
        type=int,
        help=  # noqa: E251
        "Number of processors for virtual machine. By default, only machines spawned with KVM will use multiple vCPUS."
    )
    parser.add_argument(
        "--share-folder",
        action='store_true',
        help=  # noqa: E251
        f"Share {shared_folder} with the guest using virtiofs (requires interactive, not supported with gdb)."
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=str,
        default="3m",
        help="Value to pass along to 'timeout' (default: '3m')")

    return parser.parse_args()


def arm64_have_el1_32():
    """
    Calls 'aarch64_32_bit_el1_supported' to see if 32-bit EL1 is supported on
    the current machine.

    Returns:
        True if 32-bit EL1 is supported, false if not
    """
    try:
        subprocess.run(base_folder.joinpath('utils',
                                            'aarch64_32_bit_el1_supported'),
                       check=True)
    except subprocess.CalledProcessError:
        return False
    return True


def can_use_kvm(can_test_for_kvm, guest_arch):
    """
    Checks that KVM can be used for faster VMs based on:
        * User's request
            * Whether or not '--no-kvm' was used
        * '/dev/kvm' is readable and writable by the current user
            * Implies hardware virtualization support
        * The host architecture relative to guest architecture
            * aarch64 always supports accelerated aarch64 guests, may support
              accelerated aarch32 guests
            * x86_64 always supports accelerated 64-bit and 32-bit x86 guests

    Parameters:
        can_test_for_vm (bool): False if user passed in '--no-kvm', True if not
        guest_arch (str): The guest architecture being run.

    Returns:
        True if KVM can be used based on the above parameters, False if not.
    """
    # /dev/kvm must be readable and writeable to use KVM with QEMU
    if can_test_for_kvm and os.access('/dev/kvm', os.R_OK | os.W_OK):
        host_arch = platform.machine()

        if host_arch == "aarch64":
            if guest_arch in ('arm', 'arm32_v7'):
                return arm64_have_el1_32()
            return "arm64" in guest_arch

        if host_arch == "x86_64":
            return "x86" in guest_arch

    # If we could not prove that we can use KVM safely, don't try
    return False


def get_smp_value(args):
    """
    Get the value of '-smp' based on user input and kernel configuration.
        1. If '--smp' is supplied by the user, it is used unconditionally.
        2. If '--smp' is not supplied by the user, attempt to locate the
           .config file (see comment below for logic).
        3. If the .config can be found, the upper bound of '-smp' is
           CONFIG_NR_CPUS.
        4. If the .config cannot be found, the upper bound of '-smp' is 8.
        5. Get the number of usable cores in the system.
        6. Return the smaller number between the limit from steps 3/4 and the
           number of cores in the system from step 5.

    Parameters:
        args (Namespace): The Namespace object returned from parse_arguments()

    Returns:
        The smaller number between the number of usable cores in the system and
        CONFIG_NR_CPUS.
    """
    # If the user specified a value, use it
    if args.smp:
        return args.smp

    # kernel_location is either a path to the kernel source or a full kernel
    # location. If it is a file, we need to strip off the basename so that we
    # can easily navigate around with '..'.
    kernel_dir = Path(args.kernel_location)
    if kernel_dir.is_file():
        kernel_dir = kernel_dir.parent

    # If kernel_location is the kernel source, the configuration will be at
    # <kernel_dir>/.config
    #
    # If kernel_location is a full kernel location, it could either be:
    #   * <kernel_dir>/.config (if the image is vmlinux)
    #   * <kernel_dir>/../../../.config (if the image is in arch/*/boot/)
    #   * <kernel_dir>/config (if the image is in a TuxMake folder)
    config_file = None
    for config_name in [".config", "../../../.config", "config"]:
        config_path = kernel_dir.joinpath(config_name)
        if config_path.is_file():
            config_file = config_path
            break

    # Choose a sensible default value based on treewide defaults for
    # CONFIG_NR_CPUS then get the actual value if possible.
    config_nr_cpus = 8
    if config_file:
        with open(config_file, encoding='utf-8') as file:
            for line in file:
                if "CONFIG_NR_CPUS=" in line:
                    config_nr_cpus = int(line.split("=", 1)[1])
                    break

    # Use the minimum of the number of usable processors for the script or
    # CONFIG_NR_CPUS.
    usable_cpus = os.cpu_count()
    return min(usable_cpus, config_nr_cpus)


def setup_cfg(args):
    """
    Sets up the global configuration based on user input.

    Meaning of each key:

        * append: The additional values to pass to the kernel command line.
        * architecture: The guest architecture from the list of supported
                        architectures.
        * efi: Whether or not to boot the guest under UEFI (arm64 and x86_64
               only).
        * gdb: Whether or not the user wants to debug the kernel using GDB.
        * gdb_bin: The name of or path to the GDB executable that the user
                   wants to debug with.
        * interactive: Whether or not the user is going to be running the
                       machine interactively.
        * kernel_location: The full path to the kernel image or build folder.
        * share_folder_with_guest: Share a folder on the host with a guest.
        * smp_requested: Whether or not the user specified a value with
                         '--smp'.
        * smp_value: The value to use with '-smp' (will be used when
                     smp_requested is True or using KVM).
        * timeout: The value to pass along to 'timeout' if not running
                   interactively.
        * use_kvm: Whether or not KVM will be used.

    Parameters:
        args (Namespace): The Namespace object returned from parse_arguments()

    Returns:
        A dictionary of configuration values
    """
    return {
        # Required
        "architecture": args.architecture,
        "kernel_location": Path(args.kernel_location).resolve(),

        # Optional
        "append": args.append,
        "efi": args.efi,
        "gdb": args.gdb,
        "gdb_bin": args.gdb_bin,
        "interactive": args.interactive or args.gdb,
        "share_folder_with_guest": args.share_folder,
        "smp_requested": args.smp is not None,
        "smp_value": get_smp_value(args),
        "timeout": args.timeout,
        "use_kvm": can_use_kvm(not args.no_kvm, args.architecture),
    }


def get_qemu_ver_string(qemu):
    """
    Prints the first line of QEMU's version output.

    Parameters:
        qemu (str): The QEMU executable name or path to get the version of.

    Returns:
        The first line of the QEMU version output.
    """
    utils.check_cmd(qemu)
    qemu_version_call = subprocess.run([qemu, "--version"],
                                       capture_output=True,
                                       check=True)
    # Equivalent of 'head -1'
    return qemu_version_call.stdout.decode("UTF-8").split("\n")[0]


def get_qemu_ver_tuple(qemu):
    """
    Prints QEMU's version as an integer with at least six digits.

    Errors if the requested QEMU could not be found.

    Parameters:
        qemu (str): The QEMU executable name or path to get the version of.

    Returns:
        The QEMU version as an integer with at least six digits.
    """
    qemu_version_string = get_qemu_ver_string(qemu)
    # "QEMU emulator version x.y.z (...)" -> x.y.z -> ['x', 'y', 'z']
    qemu_version = qemu_version_string.split(" ")[3].split(".")

    return tuple(int(x) for x in qemu_version)


def get_linux_ver_tuple(decomp_cmd):
    """
    Searches the Linux kernel binary for the version string using 'strings'
    then prints it as an integer with at least six digits.

    Errors if the decompression executable could not be found.

    Parameters:
        decomp_cmd (list): A list with the decompression command plus arguments
                           to decompress the kernel to stdout.

    Returns:
        The Linux kernel version as an integer with at least six digits.
    """
    decomp_exec = decomp_cmd[0]
    utils.check_cmd(decomp_exec)
    decomp = subprocess.run(decomp_cmd, capture_output=True, check=True)

    utils.check_cmd("strings")
    strings = subprocess.run(["strings"],
                             capture_output=True,
                             check=True,
                             input=decomp.stdout)

    linux_version = None
    for line in strings.stdout.decode("UTF-8", "ignore").split("\n"):
        if re.search(r"Linux version \d+\.\d+\.\d+", line):
            linux_version = re.search(r"\d+\.\d+\.\d+", line)[0].split(".")
            break
    if not linux_version:
        kernel_path = decomp_cmd[-1]
        utils.die(
            f"Linux version string could not be found in '{kernel_path}'")

    return tuple(int(x) for x in linux_version)


def get_and_decomp_rootfs(cfg):
    """
    Decompress and get the full path of the initial ramdisk for use with QEMU's
    '-initrd' parameter. Handles the special cases of the arm32_* and ppc32*
    values sharing the same initial ramdisk.

    Parameters:
        cfg (dict): The configuration dictionary generated with setup_cfg().

    Returns:
        rootfs (str): The path to the decompressed rootfs file.
    """

    arch = cfg["architecture"]
    if "arm32" in arch:
        arch_rootfs_dir = "arm"
    elif "ppc32" in arch:
        arch_rootfs_dir = "ppc32"
    else:
        arch_rootfs_dir = arch
    rootfs = base_folder.joinpath("images", arch_rootfs_dir, "rootfs.cpio")

    # This could be 'rootfs.unlink(missing_ok=True)' but that was only added in
    # Python 3.8.
    if rootfs.exists():
        rootfs.unlink()

    utils.check_cmd("zstd")
    subprocess.run(["zstd", "-q", "-d", f"{rootfs}.zst", "-o", rootfs],
                   check=True)

    return rootfs


def get_efi_args(guest_arch):
    """
    Generate QEMU arguments for EFI and performing any necessary setup steps
    like preparing firmware files.

    Parameters:
        guest_arch (str): The architecture of the guest.

    Return:
        efi_args (list): A list of arguments for QEMU to boot using UEFI.
    """
    efi_img_locations = {
        "arm64": [
            Path("edk2/aarch64/QEMU_EFI.silent.fd"),  # Fedora
            Path("edk2/aarch64/QEMU_EFI.fd"),  # Arch Linux (current)
            Path("edk2-armvirt/aarch64/QEMU_EFI.fd"),  # Arch Linux (old)
            Path("qemu-efi-aarch64/QEMU_EFI.fd"),  # Debian and Ubuntu
        ],
        "x86_64": [
            Path("edk2/x64/OVMF_CODE.fd"),  # Arch Linux (current), Fedora
            Path("edk2-ovmf/x64/OVMF_CODE.fd"),  # Arch Linux (old)
            Path("OVMF/OVMF_CODE.fd"),  # Debian and Ubuntu
        ]
    }  # yapf: disable

    if guest_arch not in efi_img_locations:
        utils.yellow(
            f"EFI boot requested for unsupported architecture ('{guest_arch}'), ignoring..."
        )
        return []

    usr_share = Path('/usr/share')
    efi_img = utils.find_first_file(usr_share, efi_img_locations[guest_arch])

    if guest_arch == "arm64":
        # Sizing the images to 64M is recommended by "Prepare the firmware" section at
        # https://mirrors.edge.kernel.org/pub/linux/kernel/people/will/docs/qemu/qemu-arm64-howto.html
        efi_img_size = 64 * 1024 * 1024  # 64M

        efi_img_qemu = base_folder.joinpath("images", guest_arch, "efi.img")
        shutil.copyfile(efi_img, efi_img_qemu)
        efi_img_qemu.open(mode="r+b").truncate(efi_img_size)

        efi_vars_qemu = base_folder.joinpath("images", guest_arch,
                                             "efivars.img")
        efi_vars_qemu.unlink(missing_ok=True)
        efi_vars_qemu.open(mode="xb").truncate(efi_img_size)

    elif guest_arch == "x86_64":
        efi_img_qemu = efi_img  # This is just usable, it is marked read only

        # Copy base EFI variables file
        efi_vars_locations = [
            Path("edk2/x64/OVMF_VARS.fd"),  # Arch Linux and Fedora
            Path("OVMF/OVMF_VARS.fd"),  # Debian and Ubuntu
        ]
        efi_vars = utils.find_first_file(usr_share, efi_vars_locations)
        efi_vars_qemu = base_folder.joinpath("images", guest_arch,
                                             efi_vars.name)
        shutil.copyfile(efi_vars, efi_vars_qemu)

    # The RNG is included to get the benefits of a KASLR seed on arm64
    # and it does not hurt x86_64.
    return [
        "-drive", f"if=pflash,format=raw,file={efi_img_qemu},readonly=on",
        "-drive", f"if=pflash,format=raw,file={efi_vars_qemu}",
        "-object", "rng-random,filename=/dev/urandom,id=rng0",
        "-device", "virtio-rng-pci"
    ]  # yapf: disable


def get_qemu_args(cfg):
    """
    Generate the QEMU command from the QEMU executable and parameters, based on
    a variety of factors:
        * User's input
        * Whether or not KVM is being used
            * A different executable and options might be needed
        * QEMU and Linux kernel version
        * Locations of firmwares and device tree blobs

    Parameters:
        cfg (dict): The configuration dictionary generated with setup_cfg().

    Returns:
        cfg (dict): The configuration dictionary updated with the QEMU command.
    """
    # Static values from cfg
    arch = cfg["architecture"]
    efi = cfg["efi"]
    kernel_location = cfg["kernel_location"]
    gdb = cfg["gdb"]
    interactive = cfg["interactive"]
    smp_requested = cfg["smp_requested"]
    smp_value = cfg["smp_value"]
    use_kvm = cfg["use_kvm"]

    # Default values, may be overwritten or modified below
    append = cfg["append"]
    dtb = None
    kernel = None
    kernel_arch = arch
    kernel_image = "zImage"
    kvm_cpu = "host"
    ram = "512m"
    qemu_args = []

    if arch == "arm32_v5":
        append += " earlycon"
        dtb = "aspeed-bmc-opp-palmetto.dtb"
        kernel_arch = "arm"
        qemu_args += ["-machine", "palmetto-bmc"]
        qemu = "qemu-system-arm"

    elif arch == "arm32_v6":
        dtb = "aspeed-bmc-opp-romulus.dtb"
        kernel_arch = "arm"
        qemu = "qemu-system-arm"
        qemu_args += ["-machine", "romulus-bmc"]

    elif arch in ("arm", "arm32_v7"):
        append += " console=ttyAMA0 earlycon"
        kernel_arch = "arm"
        qemu_args += ["-machine", "virt"]
        if use_kvm:
            kvm_cpu += ",aarch64=off"
            qemu = "qemu-system-aarch64"
        else:
            qemu = "qemu-system-arm"

    elif arch in ("arm64", "arm64be"):
        append += " console=ttyAMA0 earlycon"
        kernel_arch = "arm64"
        kernel_image = "Image.gz"
        qemu = "qemu-system-aarch64"
        machine = "virt,gic-version=max"

        if not use_kvm:
            cpu = "max"
            kernel = utils.get_full_kernel_path(kernel_location, kernel_image,
                                                kernel_arch)
            qemu_ver = get_qemu_ver_tuple(qemu)

            if qemu_ver >= (6, 2, 50):
                gzip_kernel_cmd = ["gzip", "-c", "-d", kernel]
                linux_ver = get_linux_ver_tuple(gzip_kernel_cmd)

                # https://gitlab.com/qemu-project/qemu/-/issues/964
                if linux_ver < (4, 16, 0):
                    cpu = "cortex-a72"
                # https://gitlab.com/qemu-project/qemu/-/commit/69b2265d5fe8e0f401d75e175e0a243a7d505e53
                elif linux_ver < (5, 12, 0):
                    cpu += ",lpa2=off"

            # https://lore.kernel.org/YlgVa+AP0g4IYvzN@lakrids/
            if "max" in cpu and qemu_ver >= (6, 0, 0):
                cpu += ",pauth-impdef=true"

            qemu_args += ["-cpu", cpu]
            # Boot with VHE emulation, which allows the kernel to run at EL2.
            # KVM does not emulate VHE, so this cannot be unconditional.
            machine += ",virtualization=true"

        qemu_args += ["-machine", machine]

    elif arch == "m68k":
        append += " console=ttyS0,115200"
        kernel_image = "vmlinux"
        qemu = "qemu-system-m68k"
        qemu_args += ["-cpu", "m68040"]
        qemu_args += ["-M", "q800"]

    elif arch in ("mips", "mipsel"):
        kernel_arch = "mips"
        kernel_image = "vmlinux"
        qemu = f"qemu-system-{arch}"
        qemu_args += ["-cpu", "24Kf"]
        qemu_args += ["-machine", "malta"]

    elif "ppc32" in arch:
        if arch == "ppc32":
            kernel_image = "uImage"
            qemu_args += ["-machine", "bamboo"]
        elif arch == "ppc32_mac":
            kernel_image = "vmlinux"
            qemu_args += ["-machine", "mac99"]

        append += " console=ttyS0"
        kernel_arch = "powerpc"
        qemu = "qemu-system-ppc"
        ram = "128m"

    elif arch == "ppc64":
        kernel_arch = "powerpc"
        kernel_image = "vmlinux"
        qemu = "qemu-system-ppc64"
        qemu_args += ["-cpu", "power8"]
        qemu_args += ["-machine", "pseries"]
        qemu_args += ["-vga", "none"]
        ram = "1G"

    elif arch == "ppc64le":
        kernel_arch = "powerpc"
        kernel_image = "zImage.epapr"
        qemu = "qemu-system-ppc64"
        qemu_args += ["-device", "ipmi-bmc-sim,id=bmc0"]
        qemu_args += ["-device", "isa-ipmi-bt,bmc=bmc0,irq=10"]
        qemu_args += ["-machine", "powernv"]
        ram = "2G"

    elif arch == "riscv":
        append += " earlycon"
        kernel_image = "Image"

        bios = "default"
        deb_bios = Path(
            "/usr/lib/riscv64-linux-gnu/opensbi/qemu/virt/fw_jump.elf")
        if "BIOS" in os.environ:
            bios = os.environ["BIOS"]
        elif deb_bios.exists():
            bios = deb_bios

        qemu = "qemu-system-riscv64"
        qemu_args += ["-bios", bios]
        qemu_args += ["-M", "virt"]

    elif arch == "s390":
        kernel_image = "bzImage"
        qemu = "qemu-system-s390x"
        qemu_args += ["-M", "s390-ccw-virtio"]

    elif "x86" in arch:
        append += " console=ttyS0 earlycon=uart8250,io,0x3f8"
        kernel_image = "bzImage"

        if use_kvm and not efi:
            qemu_args += ["-d", "unimp,guest_errors"]
        elif arch == "x86_64":
            qemu_args += ["-cpu", "Nehalem"]

        if arch == "x86":
            qemu = "qemu-system-i386"
        else:
            qemu = "qemu-system-x86_64"

    # Make sure QEMU is available in PATH, otherwise there is little point to
    # continuing.
    utils.check_cmd(qemu)

    # '-kernel'
    if not kernel:
        kernel = utils.get_full_kernel_path(kernel_location, kernel_image,
                                            kernel_arch)
    qemu_args += ["-kernel", kernel]

    # '-dtb'
    if dtb:
        # If we are in a boot folder, look for them in the dts folder in it
        if "boot" in str(kernel):
            dtb_dir = "dts"
        # Otherwise, assume there is a dtbs folder in the same folder as the
        # kernel image (tuxmake)
        else:
            dtb_dir = "dtbs"

        dtb = kernel.parent.joinpath(dtb_dir, dtb)
        if not dtb.exists():
            utils.die(
                f"'{dtb.stem}' is required for booting but it could not be found at '{dtb}'"
            )

        qemu_args += ["-dtb", dtb]

    # '-append'
    if gdb:
        append += " nokaslr"
    if interactive:
        append += " rdinit=/bin/sh"
    if len(append) > 0:
        qemu_args += ["-append", append.strip()]

    # Handle UEFI firmware if necessary
    if efi:
        qemu_args += get_efi_args(arch)

    # KVM and '-smp'
    if use_kvm:
        qemu_args += ["-cpu", kvm_cpu]
        qemu_args += ["-enable-kvm"]
        qemu_args += ["-smp", str(smp_value)]
    else:
        # By default, we do not use '-smp' with TCG for performance reasons.
        # Only add it if the user explicitly requested it.
        if smp_requested:
            qemu_args += ["-smp", str(smp_value)]

    # Other miscellaneous options
    qemu_args += ["-display", "none"]
    qemu_args += ["-initrd", get_and_decomp_rootfs(cfg)]
    qemu_args += ["-m", ram]
    qemu_args += ["-nodefaults"]
    qemu_args += ["-no-reboot"]

    # Resolve the full path to QEMU for the command, as recommended for use
    # with subprocess.Popen()
    qemu = shutil.which(qemu)

    cfg["qemu_cmd"] = [qemu] + qemu_args

    return cfg


def pretty_print_qemu_info(qemu):
    """
    Prints where QEMU is being used from and its version. Useful for making
    sure a specific version of QEMU is being used.

    Parameters:
        qemu (str): A string containing the full path to the QEMU executable.
    """
    qemu_dir = Path(qemu).parent
    qemu_version_string = get_qemu_ver_string(qemu)

    utils.green(f"QEMU location: \033[0m{qemu_dir}")
    utils.green(f"QEMU version: \033[0m{qemu_version_string}\n")


def pretty_print_qemu_cmd(qemu_cmd):
    """
    Prints the QEMU command in a "pretty" manner, similar to how 'set -x' works in bash.
        * Surrounds list elements that have spaces with quotation marks so that
          copying and pasting the command in a shell works.
        * Prints the QEMU executable as just the executable name, rather than
          the full path. This is done purely for aesthetic reasons, as the
          executable would normally be called with just its name through PATH
          but subprocess.Popen() recommends using a full path for maximum
          compatibility so it was generated in get_qemu_args().

    Parameters:
        qemu_cmd (list): QEMU command list.
    """
    qemu_cmd_pretty = ""
    for element in qemu_cmd:
        if " " in str(element):
            qemu_cmd_pretty += f' "{element}"'
        elif "qemu-system-" in str(element):
            qemu_cmd_pretty += f' {element.split("/")[-1]}'
        else:
            qemu_cmd_pretty += f" {element}"
    print(f"$ {qemu_cmd_pretty.strip()}", flush=True)


def launch_qemu(cfg):
    """
    Runs the QEMU command generated from get_qemu_args(), depending on whether
    or not the user wants to debug with GDB.

    If debugging with GDB, QEMU is called with '-s -S' in the background then
    gdb_bin is called against 'vmlinux' connected to the target remote. This
    can be repeated multiple times.

    Otherwise, QEMU is called with 'timeout' so that it is terminated if there
    is a problem while booting, passing along any error code that is returned.

    Parameters:
        cfg (dict): The configuration dictionary generated with setup_cfg().
    """
    interactive = cfg["interactive"]
    gdb = cfg["gdb"]
    gdb_bin = cfg["gdb_bin"]
    kernel_location = cfg["kernel_location"]
    qemu_cmd = cfg["qemu_cmd"]
    share_folder_with_guest = cfg["share_folder_with_guest"]
    timeout = cfg["timeout"]

    if share_folder_with_guest and not interactive:
        utils.yellow(
            'Shared folder requested without an interactive session, ignoring...'
        )
        share_folder_with_guest = False
    if share_folder_with_guest and gdb:
        utils.yellow(
            'Shared folder requested during a debugging session, ignoring...')
        share_folder_with_guest = False

    if share_folder_with_guest:
        shared_folder.mkdir(exist_ok=True, parents=True)

        # If shared folder was requested, we need to search for virtiofsd in
        # certain known locations.
        qemu_prefix = Path(qemu_cmd[0]).resolve().parent.parent
        virtiofsd_locations = [
            Path('libexec', 'virtiofsd'),  # Default QEMU installation, Fedora
            Path('lib', 'qemu', 'virtiofsd'),  # Arch Linux, Debian, Ubuntu
        ]
        virtiofsd = utils.find_first_file(qemu_prefix, virtiofsd_locations)

        if not (sudo := shutil.which('sudo')):
            raise Exception(
                'sudo is required to use virtiofsd but it could not be found!')
        utils.green(
            'Requesting sudo permission to run virtiofsd in the background...')
        subprocess.run([sudo, 'true'], check=True)

        virtiofsd_log = base_folder.joinpath('.vfsd.log')
        virtiofsd_mem = base_folder.joinpath('.vfsd.mem')
        virtiofsd_socket = base_folder.joinpath('.vfsd.sock')
        virtiofsd_cmd = [
            sudo,
            virtiofsd,
            f"--socket-group={grp.getgrgid(os.getgid()).gr_name}",
            f"--socket-path={virtiofsd_socket}",
            '-o', f"source={shared_folder}",
            '-o', 'cache=always',
        ]  # yapf: disable

        qemu_mem = qemu_cmd[qemu_cmd.index('-m') + 1]
        qemu_cmd += [
            '-chardev', f"socket,id=char0,path={virtiofsd_socket}",
            '-device', 'vhost-user-fs-pci,queue-size=1024,chardev=char0,tag=shared',
            '-object', f"memory-backend-file,id=shm,mem-path={virtiofsd_mem},share=on,size={qemu_mem}",
            '-numa', 'node,memdev=shm',
        ]  # yapf: disable

    # Print information about the QEMU binary
    pretty_print_qemu_info(qemu_cmd[0])

    if gdb:
        utils.check_cmd(gdb_bin)
        qemu_cmd += ["-s", "-S"]

        while True:
            utils.check_cmd("lsof")
            lsof = subprocess.run(["lsof", "-i:1234"],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  check=False)
            if lsof.returncode == 0:
                utils.die("Port 1234 is already in use, is QEMU running?")

            utils.green("Starting QEMU with GDB connection on port 1234...")
            with subprocess.Popen(qemu_cmd,
                                  preexec_fn=os.setpgrp) as qemu_process:
                utils.green("Starting GDB...")
                gdb_cmd = [gdb_bin]
                gdb_cmd += [kernel_location.joinpath("vmlinux")]
                gdb_cmd += ["-ex", "target remote :1234"]

                with subprocess.Popen(gdb_cmd) as gdb_process:
                    try:
                        gdb_process.wait()
                    except KeyboardInterrupt:
                        pass

                utils.red("Killing QEMU...")
                qemu_process.kill()

            answer = input("Re-run QEMU + gdb? [y/n] ")
            if answer.lower() == "n":
                break
    else:
        qemu_cmd += ["-serial", "mon:stdio"]

        if not interactive:
            timeout_cmd = ["timeout", "--foreground", timeout]
            stdbuf_cmd = ["stdbuf", "-oL", "-eL"]
            qemu_cmd = timeout_cmd + stdbuf_cmd + qemu_cmd

        pretty_print_qemu_cmd(qemu_cmd)
        null_cm = contextlib.nullcontext()
        with open(virtiofsd_log, 'w', encoding='utf-8') if share_folder_with_guest else null_cm as vfsd_log, \
             subprocess.Popen(virtiofsd_cmd, stderr=vfsd_log, stdout=vfsd_log) if share_folder_with_guest else null_cm as vfsd_process:
            try:
                subprocess.run(qemu_cmd, check=True)
            except subprocess.CalledProcessError as ex:
                if ex.returncode == 124:
                    utils.red("ERROR: QEMU timed out!")
                else:
                    utils.red("ERROR: QEMU did not exit cleanly!")
                    # If virtiofsd is dead, it is pretty likely that it was the
                    # cause of QEMU failing so add to the existing exception using
                    # 'from'.
                    if vfsd_process and vfsd_process.poll():
                        vfsd_log_txt = virtiofsd_log.read_text(
                            encoding='utf-8')
                        raise Exception(
                            f"virtiofsd failed with: {vfsd_log_txt}") from ex
                sys.exit(ex.returncode)
            finally:
                if vfsd_process:
                    vfsd_process.kill()
                    # Delete the memory to save space, it does not have to be
                    # persistent
                    virtiofsd_mem.unlink(missing_ok=True)


if __name__ == '__main__':
    arguments = parse_arguments()

    # Build configuration from arguments and QEMU flags
    config = setup_cfg(arguments)
    config = get_qemu_args(config)

    launch_qemu(config)
