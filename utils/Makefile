ifneq ($(shell uname -m),aarch64)
CROSS_COMPILE = aarch64-linux-gnu-
endif

CC = $(CROSS_COMPILE)gcc
STRIP = $(CROSS_COMPILE)strip

aarch64_32_bit_el1_supported: aarch64_32_bit_el1_supported.c
	$(CC) -O2 -static -std=c17 -Wall -Wextra -Wpedantic -o $@ $^
	$(STRIP) -s $@

clean:
	rm -fr aarch64_32_bit_el1_supported

all: aarch64_32_bit_el1_supported
