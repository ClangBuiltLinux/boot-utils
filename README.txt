Usage: ./boot-qemu.sh <options>

Script description: Boots a Linux kernel in QEMU.

Required parameters:
  -a | --arch | --architecture:
    The architecture to boot. Possible values are:
       * arm32_v5
       * arm32_v6
       * arm32_v7
       * arm64
       * mips
       * mipsel
       * ppc32
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

  -t | --timeout:
    By default, the timeout command waits 3 minutes before killing the
    QEMU machine. Depending on the power of the host machine, this might
    not be long enough for a kernel to boot so this allows that timeout
    to be configured. Takes the value passed to timeout (e.g. 30s or 4m).
