#include <errno.h>	/* errno for perror() */
#include <linux/kvm.h>	/* KVM_CHECK_EXTENSION, KVM_CAP_ARM_EL1_32BIT */
#include <fcntl.h>	/* open() */
#include <stdio.h>	/* perror() */
#include <sys/ioctl.h>	/* ioctl() */
#include <unistd.h>	/* close() */

int main(void)
{
	int fd, ret;

	fd = open("/dev/kvm", O_RDWR);
	if (fd < 0) {
		perror("Failed to open /dev/kvm");
		return -errno;
	}

	ret = ioctl(fd, KVM_CHECK_EXTENSION, KVM_CAP_ARM_EL1_32BIT);
	if (ret < 0) {
		perror("Error checking /dev/kvm for 32-bit EL1 support");
		ret = 0;
	}

	close(fd);

	/*
	 * KVM_CHECK_EXTENSION returns 1 for supported, 0 for unsupported so
	 * invert it to match typical success/fail codes in programs.
	 */
	return !ret;
}
