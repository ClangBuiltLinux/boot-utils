Usage: ./boot-qemu.sh <options>

Script description: Boots a Linux kernel in QEMU.

Required parameters:
  -a | --arch | --architecture:
    The architecture to boot. Possible values are:
       * arm32_v5
       * arm32_v6
       * arm32_v7
       * arm (shorthand for arm32_v7)
       * arm64
       * arm64be
       * m68k
       * mips
       * mipsel
       * ppc32
       * ppc32_mac
       * ppc64
       * ppc64le
       * riscv
       * s390
       * x86
       * x86_64

  -k | --kernel-location:
    The kernel location, which can either be the kernel image itself or
    the root of the kernel build output folder. Either option can be
    passed as an absolute path or relative path from wherever the script
    is being run.

Optional parameters:
  -d | --debug:
    Invokes 'set -x' for debugging the script.

  --debian:
    By default, the script boots a very simple Busybox based root filesystem.
    This option allows the script to boot a full Debian root filesystem,
    which can be built using 'build.sh' in the debian folder. Run

    $ sudo debian/build.sh -h

    for more information on that script.

    The kernel should be built with the 'kvm_guest.config' target to boot
    successfully. For example on an x86_64 host,

    $ make defconfig kvm_guest.config bzImage

    will produce a bootable kernel image.

  -g | --gdb:
    Add '-s -S' to the QEMU invocation to allow debugging via GDB (will invoke
   `$GDB_BIN` env var else `gdb-multiarch`).

  -h | --help:
    Prints this message then exits.

  -i | --interactive | --shell:
    By default, the rootfs images in this repo just boots the kernel,
    print the version string, then exit. If you would like to actually
    interact with the machine, this option passes 'rdinit=/bin/sh' to
    the kernel command line so that you are thrown into an interactive
    shell. When this is set, there is no timeout so any value supplied
    via the script's -t option is ignored.

  --no-kvm:
    By default, the script passes '-enable-kvm' to QEMU for hardware
    virtualization support if the host machine supports it. The option
    prevents that, causing QEMU to fallback to software virtualization.
    This can be useful for reproducing certain bugs but booting kernels
    will be much slower.

  -s | --smp:
    By default, the script does not specify a number of cores for the
    QEMU machine, which usually means it spawns with only one core.
    Certain features such as the KCSAN KUnit tests require multiple cores
    to work so this value will be used for the number of cores for the
    virtual machine. It can be more than the number of processors on your
    host machine.

  -t | --timeout:
    By default, the timeout command waits 3 minutes before killing the
    QEMU machine. Depending on the power of the host machine, this might
    not be long enough for a kernel to boot so this allows that timeout
    to be configured. Takes the value passed to timeout (e.g. 30s or 4m).
