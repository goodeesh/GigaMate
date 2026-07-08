savedcmd_gigamate_acpi.mod := printf '%s\n'   gigamate_acpi.o | awk '!x[$$0]++ { print("./"$$0) }' > gigamate_acpi.mod
