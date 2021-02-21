Usage: ./build.sh <options>

Script description: Builds a Debian filesystem image that can be booted in QEMU.

Required parameters:
  -a | --arch:
    The architecture to build the image for. Possible values are:
      * arm
      * arm64
      * ppc64le
      * s390
      * x86_64

Optional parameters:
  -l | --ltp:
    Builds some test cases from the Linux Test Project that are useful for
    finding issues.

  -m | --modules-folder:
    Path to the "modules" folder in a Linux kernel build tree. They will be
    copied into /lib within the image. For example,

    $ make INSTALL_MOD_PATH=rootfs modules_install

    in a kernel tree will place the modules folder within rootfs/lib/modules
    so the value that is passed to this script would be
    <full_linux_path_to_kernel_folder>/rootfs/lib/modules. This is useful for
    testing that kernel modules can load as well as verifying additional
    functionality within QEMU.

  -p | --password:
    The created user account's password. By default, it is just "password".

  -u | --user:
    The created user account's name. By default, it is just "user".

  -v | --version:
    The version of Debian to build. By default, it is the latest stable which
    is currently Buster.
