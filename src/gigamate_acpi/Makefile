# GigaMate ACPI kernel module
# Build:   make
#          make CC=clang LLVM=1    (if kernel was built with clang)
# Install: sudo make install
# Load:    sudo modprobe gigamate_acpi

obj-m := gigamate_acpi.o

KERNEL_BUILD := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

all:
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules

clean:
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) clean

install:
	$(MAKE) -C $(KERNEL_BUILD) M=$(PWD) modules_install
	depmod -a

.PHONY: all clean install
