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
#include <linux/mutex.h>
#include <linux/sysfs.h>
#include <linux/stat.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#define DRIVER_NAME "gigamate_acpi"
#define DRIVER_VERSION "3.0.0"

static acpi_handle amw0_handle;
static struct platform_device *gigamate_pdev;
static int current_profile = -1; /* unknown */
static int current_charge_limit = -1; /* unknown */
static DEFINE_MUTEX(gigamate_acpi_mutex);

/* ────────────────────────────────────────────
 * ACPI helpers
 * ──────────────────────────────────────────── */

static int acpi_wmbc_read_locked(u8 cmd)
{
	union acpi_object args[3];
	struct acpi_object_list input = { 3, args };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	union acpi_object *result;
	int ret = -EIO;

	if (!amw0_handle)
		return -ENODEV;

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
	if (result && result->type == ACPI_TYPE_INTEGER) {
		u64 v = result->integer.value;
		if (v == 0xFFFFFFFFULL || v == (u64)-1LL)
			ret = -ENODATA;
		else
			ret = (int)v;
	}

	kfree(result);
	return ret;
}

static int acpi_wmbc_read(u8 cmd)
{
	int ret;
	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbc_read_locked(cmd);
	mutex_unlock(&gigamate_acpi_mutex);
	return ret;
}

static int acpi_wmbd_write_locked(u8 cmd, u64 val)
{
	union acpi_object args[3];
	struct acpi_object_list input = { 3, args };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	union acpi_object *result;
	int ret = -EIO;

	if (!amw0_handle)
		return -ENODEV;

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
	if (result && result->type == ACPI_TYPE_INTEGER) {
		u64 v = result->integer.value;
		if (v == 0xFFFFFFFFULL || v == (u64)-1LL)
			ret = -ENODATA;
		else
			ret = (int)v;
	}

	kfree(result);
	return ret;
}

static int acpi_wmbd_write(u8 cmd, u64 val)
{
	int ret;
	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbd_write_locked(cmd, val);
	mutex_unlock(&gigamate_acpi_mutex);
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
	int prof;

	mutex_lock(&gigamate_acpi_mutex);
	prof = current_profile;
	mutex_unlock(&gigamate_acpi_mutex);

	/* current_profile is only known after we set it this boot. Report an
	 * error instead of claiming a profile (previously a hardcoded "1") so
	 * userspace falls back to its persisted configuration. */
	if (prof >= 0 && prof <= 3)
		return sysfs_emit(buf, "%d\n", prof);
	return -ENODATA;
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

	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbd_write_locked(0xED, val);
	if (ret < 0) {
		mutex_unlock(&gigamate_acpi_mutex);
		return ret;
	}
	current_profile = (int)val;
	mutex_unlock(&gigamate_acpi_mutex);
	return count;
}
/* Make profile user-writable via uaccess / plugdev */
static struct device_attribute dev_attr_profile_writable = {
	.attr = { .name = "profile", .mode = 0666 },
	.show = profile_show,
	.store = profile_store,
};

/* ────────────────────────────────────────────
 * Sysfs attribute — charge_limit (read-write)
 * ──────────────────────────────────────────── */

static ssize_t charge_limit_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	int policy;
	int stop;
	int cached;

	mutex_lock(&gigamate_acpi_mutex);
	policy = acpi_wmbc_read_locked(0x64);
	stop = acpi_wmbc_read_locked(0x65);

	/* If policy query succeeded and is 0 (Standard), charge limit is 100% */
	if (policy == 0) {
		current_charge_limit = 100;
		cached = current_charge_limit;
		mutex_unlock(&gigamate_acpi_mutex);
		return sysfs_emit(buf, "100\n");
	}

	/* If custom stop percentage is valid (40..100) */
	if (stop >= 40 && stop <= 100) {
		current_charge_limit = stop;
		cached = current_charge_limit;
		mutex_unlock(&gigamate_acpi_mutex);
		return sysfs_emit(buf, "%d\n", cached);
	}

	cached = current_charge_limit;
	mutex_unlock(&gigamate_acpi_mutex);

	if (cached > 0 && cached <= 100)
		return sysfs_emit(buf, "%d\n", cached);

	return -ENODATA;
}

static ssize_t charge_limit_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	unsigned long val;
	int ret1, ret2;

	ret1 = kstrtoul(buf, 10, &val);
	if (ret1)
		return -EINVAL;

	mutex_lock(&gigamate_acpi_mutex);

	/* Accept 40..100 (0 or 100 sets standard 100% full charge) */
	if (val == 0 || val == 100) {
		/* Standard policy: 0x64 = 0 (Standard), 0x65 = 100 */
		pr_info(DRIVER_NAME ": Setting standard charging policy (100%%)\n");
		ret1 = acpi_wmbd_write_locked(0x64, 0);
		ret2 = acpi_wmbd_write_locked(0x65, 100);
		if (ret1 < 0 || ret2 < 0) {
			pr_err(DRIVER_NAME ": Failed to set standard charge policy (%d, %d)\n",
			       ret1, ret2);
			mutex_unlock(&gigamate_acpi_mutex);
			return -EIO;
		}
		current_charge_limit = 100;
		mutex_unlock(&gigamate_acpi_mutex);
		return count;
	}

	if (val < 40 || val > 100) {
		mutex_unlock(&gigamate_acpi_mutex);
		return -EINVAL;
	}

	/* Custom policy: write the stop percentage first, then select Custom
	 * (0x64 = 4). If either fails, best-effort restore the standard policy
	 * so the EC is not left in an inconsistent state. */
	pr_info(DRIVER_NAME ": Setting custom charge limit: %lu%%\n", val);
	ret2 = acpi_wmbd_write_locked(0x65, val);
	ret1 = acpi_wmbd_write_locked(0x64, 4);
	if (ret2 < 0 || ret1 < 0) {
		pr_err(DRIVER_NAME ": Failed to set charge limit (policy=%d, stop=%d); rolling back\n",
		       ret1, ret2);
		acpi_wmbd_write_locked(0x65, 100);
		acpi_wmbd_write_locked(0x64, 0);
		mutex_unlock(&gigamate_acpi_mutex);
		return -EIO;
	}

	current_charge_limit = (int)val;
	mutex_unlock(&gigamate_acpi_mutex);
	return count;
}

/* Make charge_limit user-writable via uaccess / plugdev */
static struct device_attribute dev_attr_charge_limit_writable = {
	.attr = { .name = "charge_limit", .mode = 0666 },
	.show = charge_limit_show,
	.store = charge_limit_store,
};

/* ────────────────────────────────────────────
 * File operations: create/remove attributes on probe/remove
 * ──────────────────────────────────────────── */

static const char * const amw0_paths[] = {
	"\\_SB.PC00.AMW0",       /* Intel 12th/13th/14th/15th Gen (Alder/Raptor/Meteor Lake, e.g. A16, AORUS 17X) */
	"\\_SB.PCI0.AMW0",       /* AMD Ryzen & older Intel platforms (e.g. Aero X16, Aero 15) */
	"\\_SB.AMW0",            /* Root-level ACPI device */
	"\\_SB.PC00.LPCB.AMW0",  /* Intel via LPC bus */
	"\\_SB.PCI0.LPCB.AMW0",  /* AMD/Intel via LPC bus */
	"\\_SB.PC00.LPC0.AMW0",  /* Alternative LPC naming */
	"\\_SB.PCI0.LPC0.AMW0",  /* Alternative LPC naming */
	NULL
};

static const struct device_attribute *gigamate_acpi_dev_attrs[] = {
	&dev_attr_temp1_input,
	&dev_attr_temp2_input,
	&dev_attr_fan1_input,
	&dev_attr_fan2_input,
	&dev_attr_pwm1,
	&dev_attr_pwm2,
	&dev_attr_pwm1_total,
	&dev_attr_profile_writable,
	&dev_attr_charge_limit_writable,
	NULL,
};

static int gigamate_acpi_probe(struct platform_device *pdev)
{
	const struct device_attribute **attr;
	int ret, i;

	amw0_handle = NULL;
	for (i = 0; amw0_paths[i]; i++) {
		acpi_status status = acpi_get_handle(NULL, (char *)amw0_paths[i], &amw0_handle);
		if (ACPI_SUCCESS(status)) {
			pr_info(DRIVER_NAME ": AMW0 found at %s\n", amw0_paths[i]);
			break;
		}
	}

	if (!amw0_handle) {
		pr_err(DRIVER_NAME ": AMW0 device not found in any known ACPI path\n");
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

	pr_info(DRIVER_NAME ": AMW0 interface ready\n");
	return 0;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
static void gigamate_acpi_remove(struct platform_device *pdev)
#else
static int gigamate_acpi_remove(struct platform_device *pdev)
#endif
{
	const struct device_attribute **attr;

	for (attr = gigamate_acpi_dev_attrs; *attr; attr++)
		device_remove_file(&pdev->dev, *attr);

	amw0_handle = NULL;
	current_profile = -1;
	current_charge_limit = -1;
	pr_info(DRIVER_NAME ": module removed\n");
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 11, 0)
	return 0;
#endif
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

	/* If probe failed (e.g. AMW0 device not found), driver is not bound */
	if (!gigamate_pdev->dev.driver) {
		platform_device_unregister(gigamate_pdev);
		platform_driver_unregister(&gigamate_acpi_driver);
		pr_err(DRIVER_NAME ": hardware probe failed, unloading\n");
		return -ENODEV;
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
