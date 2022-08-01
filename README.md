# Boot utilities

This repository houses scripts to quickly boot test Linux kernels with a simple [Buildroot](https://buildroot.org)-based rootfs.

* `boot-qemu.py`: Script to boot Linux kernels in QEMU. Run with `-h` for information on options.
* `boot-uml.py`: Script to boot a User Mode Linux (UML) kernel. Run with `-h` for information on options.
* `utils.py`: Common functions to Python scripts, not meant to be called.i

* `buildroot/`: Scripts and configuration files to generate rootfs images.
* `images/`: Generated rootfs images from Buildroot (compressed with `zstd`).
* `utils/`: Miscellaneous utilities/programs.
