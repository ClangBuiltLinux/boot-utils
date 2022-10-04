#!/usr/bin/env python3

import argparse
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

import utils

base_folder = Path(__file__).resolve().parent
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
        help=
        "Instead of immediately shutting down the machine upon successful boot, pass 'rdinit=/bin/sh' on the kernel command line to allow interacting with the machine via a shell."
    )
    parser.add_argument(
        "-k",
        "--kernel-location",
        required=True,
        type=str,
        help=
        "Path to kernel image or kernel build folder to search for image in. Can be an absolute or relative path."
    )
    parser.add_argument(
        "--no-kvm",
        action="store_true",
        help=
        "Do not use KVM for acceleration even when supported (only recommended for debugging)."
    )
    parser.add_argument(
        "-s",
        "--smp",
        type=int,
        help=
        "Number of processors for virtual machine. By default, only machines spawned with KVM will use multiple vCPUS."
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=str,
        default="3m",
        help="Value to pass along to 'timeout' (default: '3m')")

    return parser.parse_args()


def can_use_kvm(can_test_for_kvm, guest_arch):
    """
    Checks that KVM can be used for faster VMs based on:
        * User's request
            * Whether or not '--no-kvm' was used
        * '/dev/kvm' is available
        * The guest architecture
            * Only 'arm'/'arm32_v7', 'arm64', 'arm64be', 'x86', and 'x86_64'
              are supported with KVM
        * Availability of hardware virtualization support
            * aarch64 may not support accelerated 32-bit guests
            * i386 and x86_64 need the virtualization extensions in
              '/proc/cpuinfo'

    Parameters:
        user_kvm_opt_out (bool): False if user passed in '--no-kvm', True if not
        guest_arch (str): The guest architecture being run.

    Returns:
        True if KVM can be used based on the above parameters, False if not.
    """
    if can_test_for_kvm:
        # /dev/kvm must exist to use KVM with QEMU
        if Path("/dev/kvm").exists():
            guest_arch = args.architecture
            host_arch = platform.machine()

            if host_arch == "aarch64":
                # If /dev/kvm exists on aarch64, KVM is supported for aarch64 guests
                if "arm64" in guest_arch:
                    return True
                # 32-bit EL1 is not always supported, test for it first
                if guest_arch == "arm" or guest_arch == "arm32_v7":
                    check_32_bit_el1_exec = base_folder.joinpath(
                        "utils", "aarch64_32_bit_el1_supported")
                    check_32_bit_el1 = subprocess.run(
                        [check_32_bit_el1_exec.as_posix()])
                    return check_32_bit_el1.returncode == 0

            if host_arch == "x86_64" and "x86" in guest_arch:
                # Check /proc/cpuinfo for whether or not the machine supports hardware virtualization
                with open("/proc/cpuinfo") as f:
                    cpuinfo = f.read()
                # SVM is AMD, VMX is Intel
                return cpuinfo.count("svm") > 0 or cpuinfo.count("vmx") > 0

    # We could not prove that we could use KVM safely so don't try
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
            config_file = config_path.as_posix()
            break

    # Choose a sensible default value based on treewide defaults for
    # CONFIG_NR_CPUS then get the actual value if possible.
    config_nr_cpus = 8
    if config_file:
        with open(config_file) as f:
            for line in f:
                if "CONFIG_NR_CPUS=" in line:
                    config_nr_cpus = int(line.split("=", 1)[1])
                    break

    # Use the minimum of the number of usable processors for the script or
    # CONFIG_NR_CPUS.
    usable_cpus = len(os.sched_getaffinity(0))
    return min(usable_cpus, config_nr_cpus)


def setup_cfg(args):
    """
    Sets up the global configuration based on user input.

    Meaning of each key:

        * append: The additional values to pass to the kernel command line.
        * architecture: The guest architecture from the list of supported
                        architectures.
        * gdb: Whether or not the user wants to debug the kernel using GDB.
        * gdb_bin: The name of or path to the GDB executable that the user
                   wants to debug with.
        * interactive: Whether or not the user is going to be running the
                       machine interactively.
        * kernel_location: The full path to the kernel image or build folder.
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
        "kernel_location": Path(args.kernel_location).resolve().as_posix(),

        # Optional
        "append": args.append,
        "gdb": args.gdb,
        "gdb_bin": args.gdb_bin,
        "interactive": args.interactive or args.gdb,
        "smp_requested": args.smp is not None,
        "smp_value": get_smp_value(args),
        "timeout": args.timeout,
        "use_kvm": can_use_kvm(not args.no_kvm, args.architecture),
    }


def create_version_code(version):
    """
    Turns a version list with three values (major, minor, and patch level) into
    an integer with at least six digits:
        * major: as is
        * minor: with a minimum length of two ("1" becomes "01")
        * patch level: with a minimum length of three ("1" becomes "001")

    Parameters:
        version (list): A list with three integer values (major, minor, and
                        patch level).

    Returns:
        An integer with at least six digits.
    """
    major, minor, patch = [int(version[i]) for i in (0, 1, 2)]
    return int("{:d}{:02d}{:03d}".format(major, minor, patch))


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


def get_qemu_ver_code(qemu):
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

    return create_version_code(qemu_version)


def get_linux_ver_code(decomp_cmd):
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
    for line in strings.stdout.decode("UTF-8").split("\n"):
        if re.search(r"Linux version \d+\.\d+\.\d+", line):
            linux_version = re.search(r"\d+\.\d+\.\d+", line)[0].split(".")
            break
    if not linux_version:
        kernel_path = decomp_cmd[-1]
        utils.die("Linux version string could not be found in '{}'".format(
            kernel_path))

    return create_version_code(linux_version)


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

    # Print as string for remainder due to use in subprocess command lists
    rootfs = rootfs.as_posix()

    utils.check_cmd("zstd")
    subprocess.run(["zstd", "-q", "-d", "{}.zst".format(rootfs), "-o", rootfs],
                   check=True)

    return rootfs


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

    elif arch == "arm" or arch == "arm32_v7":
        append += " console=ttyAMA0 earlycon"
        kernel_arch = "arm"
        qemu_args += ["-machine", "virt"]
        if use_kvm:
            kvm_cpu += ",aarch64=off"
            qemu = "qemu-system-aarch64"
        else:
            qemu = "qemu-system-arm"

    elif arch == "arm64" or arch == "arm64be":
        append += " console=ttyAMA0 earlycon"
        kernel_arch = "arm64"
        kernel_image = "Image.gz"
        qemu = "qemu-system-aarch64"
        qemu_args += ["-machine", "virt,gic-version=max"]

        if not use_kvm:
            cpu = "max"
            kernel = utils.get_full_kernel_path(kernel_location, kernel_image,
                                                kernel_arch)
            qemu_ver_code = get_qemu_ver_code(qemu)

            if qemu_ver_code >= 602050:
                gzip_kernel_cmd = ["gzip", "-c", "-d", kernel.as_posix()]
                linux_ver_code = get_linux_ver_code(gzip_kernel_cmd)

                # https://gitlab.com/qemu-project/qemu/-/issues/964
                if linux_ver_code < 416000:
                    cpu = "cortex-a72"
                # https://gitlab.com/qemu-project/qemu/-/commit/69b2265d5fe8e0f401d75e175e0a243a7d505e53
                elif linux_ver_code < 512000:
                    cpu += ",lpa2=off"

            # https://lore.kernel.org/YlgVa+AP0g4IYvzN@lakrids/
            if "max" in cpu and qemu_ver_code >= 600000:
                cpu += ",pauth-impdef=true"

            qemu_args += ["-cpu", cpu]
            qemu_args += ["-machine", "virtualization=true"]

    elif arch == "m68k":
        append += " console=ttyS0,115200"
        kernel_image = "vmlinux"
        qemu = "qemu-system-m68k"
        qemu_args += ["-cpu", "m68040"]
        qemu_args += ["-M", "q800"]

    elif arch == "mips" or arch == "mipsel":
        kernel_arch = "mips"
        kernel_image = "vmlinux"
        qemu = "qemu-system-{}".format(arch)
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
            bios = deb_bios.as_posix()

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

        if use_kvm:
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
    qemu_args += ["-kernel", kernel.as_posix()]

    # '-dtb'
    if dtb:
        # If we are in a boot folder, look for them in the dts folder in it
        if "boot" in kernel.as_posix():
            dtb_dir = "dts"
        # Otherwise, assume there is a dtbs folder in the same folder as the
        # kernel image (tuxmake)
        else:
            dtb_dir = "dtbs"

        dtb = kernel.parent.joinpath(dtb_dir, dtb)
        if not dtb.exists():
            utils.die(
                "'{}' is required for booting but it could not be found at '{}'"
                .format(dtb.stem.as_posix(), dtb.as_posix()))

        qemu_args += ["-dtb", dtb.as_posix()]

    # '-append'
    if gdb:
        append += " nokaslr"
    if interactive:
        append += " rdinit=/bin/sh"
    if len(append) > 0:
        qemu_args += ["-append", append.strip()]

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
    qemu_dir = Path(qemu).parent.as_posix()
    qemu_version_string = get_qemu_ver_string(qemu)

    utils.green("QEMU location: \033[0m{}".format(qemu_dir))
    utils.green("QEMU version: \033[0m{}\n".format(qemu_version_string))


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
        if " " in element:
            qemu_cmd_pretty += ' "{}"'.format(element)
        elif "qemu-system-" in element:
            qemu_cmd_pretty += " {}".format(element.split("/")[-1])
        else:
            qemu_cmd_pretty += " {}".format(element)
    print("$ {}".format(qemu_cmd_pretty.strip()), flush=True)


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
    timeout = cfg["timeout"]

    # Print information about the QEMU binary
    pretty_print_qemu_info(qemu_cmd[0])

    if gdb:
        while True:
            utils.check_cmd("lsof")
            lsof = subprocess.run(["lsof", "-i:1234"],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
            if lsof.returncode == 0:
                utils.die("Port 1234 is already in use, is QEMU running?")

            utils.green("Starting QEMU with GDB connection on port 1234...")
            qemu_process = subprocess.Popen(qemu_cmd + ["-s", "-S"])

            utils.green("Starting GDB...")
            utils.check_cmd(gdb_bin)
            gdb_cmd = [gdb_bin]
            gdb_cmd += [Path(kernel_location).joinpath("vmlinux").as_posix()]
            gdb_cmd += ["-ex", "target remote :1234"]
            subprocess.run(gdb_cmd)

            utils.red("Killing QEMU...")
            qemu_process.kill()
            qemu_process.wait()

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
        try:
            subprocess.run(qemu_cmd, check=True)
        except subprocess.CalledProcessError as ex:
            if ex.returncode == 124:
                utils.red("ERROR: QEMU timed out!")
            else:
                utils.red("ERROR: QEMU did not exit cleanly!")
            exit(ex.returncode)


if __name__ == '__main__':
    args = parse_arguments()

    # Build configuration from arguments and QEMU flags
    cfg = setup_cfg(args)
    cfg = get_qemu_args(cfg)

    launch_qemu(cfg)
