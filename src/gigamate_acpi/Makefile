# GigaMate ACPI kernel module
# Build: make
# Install: sudo make install
# Load:   sudo modprobe gigamate_acpi

# Detect the compiler used to build the kernel
KERNEL_BUILD := /lib/modules/$(shell uname -r)/build
CC_USED := $(shell $(MAKE) -s -C $(KERNEL_BUILD) kernelrelease 2>/dev/null; cat $(KERNEL_BUILD)/include/config/CC_VERSION_TEXT 2>/dev/null | head -1 | awk '{print $$1}')

obj-m := gigamate_acpi.o
PWD := $(shell pwd)

all:
ifeq ($(CC_USED),clang)
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules CC=clang LLVM=1
else
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules
endif

clean:
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) clean

install:
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules_install
	depmod -a

.PHONY: all clean install
