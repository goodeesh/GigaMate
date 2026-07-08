/* GigaMate ACPI kernel module
 *
 * Provides sysfs interface to the AMW0 WMI device found on
 * Gigabyte Aero/AORUS laptops. Exposes fan speeds, temperatures,
 * duty cycles, and power profile switching.
 *
 * Based on acpi_fan.c from ~/wmi_rgb_test/
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/acpi.h>
#include <linux/platform_device.h>
#include <linux/device.h>
#include <linux/sysfs.h>
#include <linux/stat.h>
#include <linux/uaccess.h>

#define DRIVER_NAME "gigamate_acpi"
#define DRIVER_VERSION "1.0.0"

static acpi_handle amw0_handle;
static struct platform_device *gigamate_pdev;
static int current_profile = -1; /* unknown */

/* ────────────────────────────────────────────
 * ACPI helpers
 * ──────────────────────────────────────────── */

static int acpi_wmbc_read(u8 cmd)
{
	union acpi_object args[3];
	struct acpi_object_list input = { 3, args };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	union acpi_object *result;
	int ret = -EIO;

	args[0].type = ACPI_TYPE_INTEGER;
	args[0].integer.value = 0;
	args[1].type = ACPI_TYPE_INTEGER;
	args[1].integer.value = cmd;
	args[2].type = ACPI_TYPE_INTEGER;
	args[2].integer.value = 0;

	status = acpi_evaluate_object(amw0_handle, "WMBC", &input, &output);
	if (ACPI_FAILURE(status)) {
		pr_debug(DRIVER_NAME ": WMBC(0x%02X) failed: %d\n", cmd, status);
		return -EIO;
	}

	result = output.pointer;
	if (result && result->type == ACPI_TYPE_INTEGER)
		ret = (int)result->integer.value;

	kfree(result);
	return ret;
}

static int acpi_wmbd_write(u8 cmd, u64 val)
{
	union acpi_object args[3];
	struct acpi_object_list input = { 3, args };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	union acpi_object *result;
	int ret = -EIO;

	args[0].type = ACPI_TYPE_INTEGER;
	args[0].integer.value = 0;
	args[1].type = ACPI_TYPE_INTEGER;
	args[1].integer.value = cmd;
	args[2].type = ACPI_TYPE_INTEGER;
	args[2].integer.value = val;

	status = acpi_evaluate_object(amw0_handle, "WMBD", &input, &output);
	if (ACPI_FAILURE(status)) {
		pr_debug(DRIVER_NAME ": WMBD(0x%02X, %llu) failed: %d\n", cmd, val, status);
		return -EIO;
	}

	result = output.pointer;
	if (result && result->type == ACPI_TYPE_INTEGER)
		ret = (int)result->integer.value;

	kfree(result);
	return ret;
}

/* ────────────────────────────────────────────
 * Sysfs attributes — sensors (read-only)
 * ──────────────────────────────────────────── */

static ssize_t temp1_input_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0xE1);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(temp1_input);

static ssize_t temp2_input_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0xE2);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(temp2_input);

static ssize_t fan1_input_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0xE4);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(fan1_input);

static ssize_t fan2_input_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0xE5);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(fan2_input);

static ssize_t pwm1_show(struct device *dev,
			 struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0x46);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(pwm1);

static ssize_t pwm2_show(struct device *dev,
			 struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0x47);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(pwm2);

static ssize_t pwm1_total_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0x50);
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(pwm1_total);

/* ────────────────────────────────────────────
 * Sysfs attribute — profile (read-write)
 * ──────────────────────────────────────────── */

static ssize_t profile_show(struct device *dev,
			    struct device_attribute *attr, char *buf)
{
	if (current_profile >= 0 && current_profile <= 3)
		return sysfs_emit(buf, "%d\n", current_profile);
	return sysfs_emit(buf, "1\n"); /* safe default */
}

static ssize_t profile_store(struct device *dev,
			     struct device_attribute *attr,
			     const char *buf, size_t count)
{
	unsigned long val;
	int ret;

	ret = kstrtoul(buf, 10, &val);
	if (ret)
		return -EINVAL;

	if (val > 3)
		return -EINVAL;

	ret = acpi_wmbd_write(0xED, val);
	if (ret < 0)
		return ret;

	current_profile = (int)val;
	return count;
}
static DEVICE_ATTR_RW(profile);
/* Make profile world-writable so non-root users can change it */
static struct device_attribute dev_attr_profile_writable = {
	.attr = { .name = "profile", .mode = 0666 },
	.show = profile_show,
	.store = profile_store,
};

/* ────────────────────────────────────────────
 * File operations: create/remove attributes on probe/remove
 * ──────────────────────────────────────────── */

static const struct device_attribute *gigamate_acpi_dev_attrs[] = {
	&dev_attr_temp1_input,
	&dev_attr_temp2_input,
	&dev_attr_fan1_input,
	&dev_attr_fan2_input,
	&dev_attr_pwm1,
	&dev_attr_pwm2,
	&dev_attr_pwm1_total,
	&dev_attr_profile_writable,
	NULL,
};

static int gigamate_acpi_probe(struct platform_device *pdev)
{
	acpi_status status;
	const struct device_attribute **attr;
	int ret;

	status = acpi_get_handle(NULL, "\\_SB.PCI0.AMW0", &amw0_handle);
	if (ACPI_FAILURE(status)) {
		pr_err(DRIVER_NAME ": AMW0 device not found\n");
		return -ENODEV;
	}

	/* Create sysfs files under the device directory */
	for (attr = gigamate_acpi_dev_attrs; *attr; attr++) {
		ret = device_create_file(&pdev->dev, *attr);
		if (ret) {
			/* Remove previously created files on error */
			const struct device_attribute **a;
			for (a = gigamate_acpi_dev_attrs; a < attr; a++)
				device_remove_file(&pdev->dev, *a);
			pr_err(DRIVER_NAME ": failed to create sysfs file\n");
			return ret;
		}
	}

	pr_info(DRIVER_NAME ": AMW0 found, interface ready\n");
	return 0;
}

static void gigamate_acpi_remove(struct platform_device *pdev)
{
	const struct device_attribute **attr;

	for (attr = gigamate_acpi_dev_attrs; *attr; attr++)
		device_remove_file(&pdev->dev, *attr);

	pr_info(DRIVER_NAME ": module removed\n");
}

static struct platform_driver gigamate_acpi_driver = {
	.driver = {
		.name = DRIVER_NAME,
		.owner = THIS_MODULE,
	},
	.probe = gigamate_acpi_probe,
	.remove = gigamate_acpi_remove,
};

/* ────────────────────────────────────────────
 * Module init / exit
 * ──────────────────────────────────────────── */

static int __init gigamate_acpi_init(void)
{
	int ret;

	ret = platform_driver_register(&gigamate_acpi_driver);
	if (ret)
		return ret;

	/* Create platform device so sysfs directory appears */
	gigamate_pdev = platform_device_register_simple(DRIVER_NAME, -1,
							NULL, 0);
	if (IS_ERR(gigamate_pdev)) {
		platform_driver_unregister(&gigamate_acpi_driver);
		return PTR_ERR(gigamate_pdev);
	}

	pr_info(DRIVER_NAME ": loaded (version %s)\n", DRIVER_VERSION);
	return 0;
}

static void __exit gigamate_acpi_exit(void)
{
	platform_device_unregister(gigamate_pdev);
	platform_driver_unregister(&gigamate_acpi_driver);
	pr_info(DRIVER_NAME ": unloaded\n");
}

module_init(gigamate_acpi_init);
module_exit(gigamate_acpi_exit);

MODULE_LICENSE("GPL v2");
MODULE_AUTHOR("GigaMate Contributors");
MODULE_DESCRIPTION("GigaMate — ACPI WMI interface for Gigabyte laptops");
MODULE_VERSION(DRIVER_VERSION);
