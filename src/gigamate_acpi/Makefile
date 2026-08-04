# GigaMate ACPI kernel module
# Build:   make
#          make KDIR=/path/to/kernel/build   (override build dir)
#          make CC=clang LLVM=1    (if kernel was built with clang)
# Install: sudo make install
# Load:    sudo modprobe gigamate_acpi

obj-m := gigamate_acpi.o

KDIR ?= /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

all:
	$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD) clean

install:
	$(MAKE) -C $(KDIR) M=$(PWD) modules_install
	depmod -a

.PHONY: all clean install
