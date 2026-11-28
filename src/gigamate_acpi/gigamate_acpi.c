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
#include <linux/dmi.h>
#include <linux/pci.h>
#include <linux/ctype.h>
#include <linux/string.h>
#include <linux/platform_device.h>
#include <linux/device.h>
#include <linux/mutex.h>
#include <linux/sysfs.h>
#include <linux/stat.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#define DRIVER_NAME "gigamate_acpi"
#define DRIVER_VERSION "5.0.0"

static acpi_handle amw0_handle;
static struct platform_device *gigamate_pdev;
static int current_profile = -1; /* unknown */
static int current_charge_limit = -1; /* unknown */
static int current_cpu_stapm_w = -1;
static int current_cpu_slow_ppt_w = -1;
static int current_cpu_fast_ppt_w = -1;
static bool has_attr_cpu_stapm;
static bool has_attr_cpu_slow_ppt;
static bool has_attr_cpu_fast_ppt;
static bool has_attr_gpu_ctgp_offset;
static bool has_attr_gpu_dynamic_boost;
static char cached_dgpu_acpi_path[128];
static char last_eval_result[128];
static bool has_eval_result;
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

/* Variant for WMBD opcodes (e.g. 0xF1-0xF3, 0x4C) that do not execute Return(...)
 * and therefore yield output.pointer == NULL on AE_OK. */
static int acpi_wmbd_exec_locked(u8 cmd, u64 val)
{
	union acpi_object args[3];
	struct acpi_object_list input = { 3, args };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	union acpi_object *result;
	int ret = 0;

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

static ssize_t usbc_adapter_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	int val = acpi_wmbc_read(0xA2);
	if (val < 0 || val > 1)
		return -ENODATA;
	return sysfs_emit(buf, "%d\n", val);
}
static DEVICE_ATTR_RO(usbc_adapter);

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
	/* WMBD(0xED) reloads stock EC CPU PPT limits for the new profile. */
	current_cpu_stapm_w = -1;
	current_cpu_slow_ppt_w = -1;
	current_cpu_fast_ppt_w = -1;
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
 * Sysfs attributes — NVIDIA NVPCF (read-only) & WMBD AML slice
 * ──────────────────────────────────────────── */

#define WMBD_AML_MAX_LEN 4096
static u8 wmbd_aml_buf[WMBD_AML_MAX_LEN];
static size_t wmbd_aml_len;

static int acpi_npcf_read(const char *field)
{
	char path[32];
	unsigned long long val = 0;
	acpi_status status;

	snprintf(path, sizeof(path), "\\_SB.NPCF.%s", field);
	status = acpi_evaluate_integer(NULL, path, NULL, &val);
	if (ACPI_FAILURE(status))
		return -ENODATA;
	return (int)val;
}

#define DEFINE_NPCF_ATTR(lname, uname)					\
static ssize_t npcf_##lname##_show(struct device *dev,			\
				   struct device_attribute *attr,	\
				   char *buf)				\
{									\
	int val = acpi_npcf_read(#uname);				\
	if (val < 0)							\
		return val;						\
	return sysfs_emit(buf, "%d\n", val);				\
}									\
static DEVICE_ATTR_RO(npcf_##lname)

DEFINE_NPCF_ATTR(atpp, ATPP);
DEFINE_NPCF_ATTR(acbt, ACBT);
DEFINE_NPCF_ATTR(amat, AMAT);
DEFINE_NPCF_ATTR(dbac, DBAC);

static ssize_t wmbd_aml_read(struct file *filp, struct kobject *kobj,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
			     const struct bin_attribute *attr,
#else
			     struct bin_attribute *attr,
#endif
			     char *buf, loff_t off, size_t count)
{
	if (wmbd_aml_len == 0)
		return -ENODATA;
	return memory_read_from_buffer(buf, count, &off, wmbd_aml_buf, wmbd_aml_len);
}

static struct bin_attribute bin_attr_wmbd_aml = {
	.attr = { .name = "wmbd_aml", .mode = 0444 },
	.size = WMBD_AML_MAX_LEN,
	.read = wmbd_aml_read,
};

static void gigamate_cache_wmbd_aml(void)
{
	struct acpi_table_header *dsdt = NULL;
	acpi_status status;
	const u8 *base;
	u32 len, i;

	wmbd_aml_len = 0;
	status = acpi_get_table(ACPI_SIG_DSDT, 0, &dsdt);
	if (ACPI_FAILURE(status) || !dsdt || dsdt->length <= sizeof(*dsdt) + 4)
		return;

	base = (const u8 *)dsdt;
	len = dsdt->length;
	for (i = sizeof(*dsdt); i + 5 <= len; i++) {
		if (base[i] == 'W' && base[i + 1] == 'M' &&
		    base[i + 2] == 'B' && base[i + 3] == 'D' &&
		    (base[i + 4] & 0x07) == 0x03) {
			size_t copy_len = len - i;
			if (copy_len > WMBD_AML_MAX_LEN)
				copy_len = WMBD_AML_MAX_LEN;
			memcpy(wmbd_aml_buf, base + i, copy_len);
			wmbd_aml_len = copy_len;
			break;
		}
	}
	/* Fallback if a BIOS declares WMBD with a different argument count */
	if (wmbd_aml_len == 0) {
		for (i = sizeof(*dsdt); i + 4 <= len; i++) {
			if (base[i] == 'W' && base[i + 1] == 'M' &&
			    base[i + 2] == 'B' && base[i + 3] == 'D') {
				size_t copy_len = len - i;
				if (copy_len > WMBD_AML_MAX_LEN)
					copy_len = WMBD_AML_MAX_LEN;
				memcpy(wmbd_aml_buf, base + i, copy_len);
				wmbd_aml_len = copy_len;
				break;
			}
		}
	}
	acpi_put_table(dsdt);
}

/* ────────────────────────────────────────────
 * WMBD AML bytecode gating & power budget attributes
 * ──────────────────────────────────────────── */

static const u8 *gigamate_memmem(const u8 *hay, size_t hlen,
				 const u8 *needle, size_t nlen)
{
	size_t i;

	if (!nlen)
		return hay;
	if (!hay || hlen < nlen)
		return NULL;
	for (i = 0; i <= hlen - nlen; i++) {
		if (memcmp(hay + i, needle, nlen) == 0)
			return hay + i;
	}
	return NULL;
}

static size_t gigamate_wmbd_body_len(void)
{
	const u8 wmbc_sig[4] = { 'W', 'M', 'B', 'C' };
	const u8 *wmbc;

	if (wmbd_aml_len <= 4)
		return wmbd_aml_len;
	wmbc = gigamate_memmem(wmbd_aml_buf + 4, wmbd_aml_len - 4, wmbc_sig, 4);
	if (wmbc)
		return (size_t)(wmbc - wmbd_aml_buf);
	return wmbd_aml_len;
}

static bool gigamate_wmbd_has_ecpt_mw_opcode(u8 opcode)
{
	const u8 op_pat[2] = { 0x0A, opcode };
	const u8 ecpt_sig[4] = { 'E', 'C', 'P', 'T' };
	const u8 div_1000[3] = { 0x0B, 0xE8, 0x03 };
	size_t body_len = gigamate_wmbd_body_len();
	size_t pos = 0;

	while (pos + sizeof(op_pat) <= body_len) {
		const u8 *hit = gigamate_memmem(wmbd_aml_buf + pos, body_len - pos,
						op_pat, sizeof(op_pat));
		size_t off, win;

		if (!hit)
			return false;
		off = (size_t)(hit - wmbd_aml_buf) + sizeof(op_pat);
		win = min_t(size_t, 64, body_len - off);
		if (gigamate_memmem(wmbd_aml_buf + off, win, ecpt_sig, sizeof(ecpt_sig)) &&
		    gigamate_memmem(wmbd_aml_buf + off, win, div_1000, sizeof(div_1000)))
			return true;
		pos = off;
	}
	return false;
}

static bool gigamate_wmbd_has_named_opcode(u8 opcode, const char name4[4])
{
	const u8 op_pat[2] = { 0x0A, opcode };
	size_t body_len = gigamate_wmbd_body_len();
	size_t pos = 0;

	while (pos + sizeof(op_pat) <= body_len) {
		const u8 *hit = gigamate_memmem(wmbd_aml_buf + pos, body_len - pos,
						op_pat, sizeof(op_pat));
		size_t off, win;

		if (!hit)
			return false;
		off = (size_t)(hit - wmbd_aml_buf) + sizeof(op_pat);
		win = min_t(size_t, 64, body_len - off);
		if (gigamate_memmem(wmbd_aml_buf + off, win, (const u8 *)name4, 4))
			return true;
		pos = off;
	}
	return false;
}

static bool gigamate_wmbd_has_ctgp_offset(void)
{
	return gigamate_wmbd_has_named_opcode(0x4C, "ACBT");
}

static bool gigamate_wmbd_has_dyn_boost(void)
{
	return gigamate_wmbd_has_named_opcode(0xE7, "DBAC");
}

static ssize_t gigamate_cpu_ppt_show_cached(int *cache_ptr, char *buf)
{
	int val;

	mutex_lock(&gigamate_acpi_mutex);
	val = *cache_ptr;
	mutex_unlock(&gigamate_acpi_mutex);
	if (val < 0)
		return -ENODATA;
	return sysfs_emit(buf, "%d\n", val);
}

static ssize_t gigamate_cpu_ppt_store_watts(u8 cmd, int *cache_ptr,
					    const char *buf, size_t count)
{
	unsigned long watts;
	int ret;

	ret = kstrtoul(buf, 10, &watts);
	if (ret)
		return -EINVAL;
	if (watts < 10 || watts > 120)
		return -EINVAL;

	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbd_exec_locked(cmd, (u64)watts * 1000ULL);
	if (ret < 0) {
		mutex_unlock(&gigamate_acpi_mutex);
		return ret;
	}
	*cache_ptr = (int)watts;
	mutex_unlock(&gigamate_acpi_mutex);
	return count;
}

#define DEFINE_CPU_PPT_ATTR(attr_name, opcode, cache_var)		\
static ssize_t attr_name##_show(struct device *dev,			\
				struct device_attribute *attr, char *buf)\
{									\
	return gigamate_cpu_ppt_show_cached(&(cache_var), buf);		\
}									\
static ssize_t attr_name##_store(struct device *dev,			\
				 struct device_attribute *attr,		\
				 const char *buf, size_t count)		\
{									\
	return gigamate_cpu_ppt_store_watts((opcode), &(cache_var),	\
					    buf, count);		\
}									\
static struct device_attribute dev_attr_##attr_name##_writable = {	\
	.attr = { .name = #attr_name, .mode = 0666 },			\
	.show = attr_name##_show,					\
	.store = attr_name##_store,					\
}

DEFINE_CPU_PPT_ATTR(cpu_stapm_w, 0xF1, current_cpu_stapm_w);
DEFINE_CPU_PPT_ATTR(cpu_slow_ppt_w, 0xF2, current_cpu_slow_ppt_w);
DEFINE_CPU_PPT_ATTR(cpu_fast_ppt_w, 0xF3, current_cpu_fast_ppt_w);

static ssize_t gpu_ctgp_offset_w_show(struct device *dev,
				      struct device_attribute *attr, char *buf)
{
	int val = acpi_npcf_read("ACBT");
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val / 8);
}

static ssize_t gpu_ctgp_offset_w_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	unsigned long val;
	int ret;

	ret = kstrtoul(buf, 10, &val);
	if (ret)
		return -EINVAL;
	if (val > 10)
		return -EINVAL;

	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbd_exec_locked(0x4C, (u64)val);
	mutex_unlock(&gigamate_acpi_mutex);
	if (ret < 0)
		return ret;
	return count;
}

static struct device_attribute dev_attr_gpu_ctgp_offset_w_writable = {
	.attr = { .name = "gpu_ctgp_offset_w", .mode = 0666 },
	.show = gpu_ctgp_offset_w_show,
	.store = gpu_ctgp_offset_w_store,
};

static ssize_t gpu_dynamic_boost_show(struct device *dev,
				      struct device_attribute *attr, char *buf)
{
	int val = acpi_npcf_read("DBAC");
	if (val < 0)
		return val;
	return sysfs_emit(buf, "%d\n", val == 0 ? 1 : 0);
}

static ssize_t gpu_dynamic_boost_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	unsigned long val;
	int ret;

	ret = kstrtoul(buf, 10, &val);
	if (ret)
		return -EINVAL;
	if (val > 1)
		return -EINVAL;

	mutex_lock(&gigamate_acpi_mutex);
	ret = acpi_wmbd_exec_locked(0xE7, (u64)val);
	mutex_unlock(&gigamate_acpi_mutex);
	if (ret < 0)
		return ret;
	return count;
}

static struct device_attribute dev_attr_gpu_dynamic_boost_writable = {
	.attr = { .name = "gpu_dynamic_boost", .mode = 0666 },
	.show = gpu_dynamic_boost_show,
	.store = gpu_dynamic_boost_store,
};

/* ────────────────────────────────────────────
 * Sysfs attributes — dGPU ACPI lookup & method evaluation
 * ──────────────────────────────────────────── */

static int gigamate_lookup_dgpu_acpi_path_locked(char *out, size_t out_len)
{
	struct pci_dev *pdev = NULL;
	struct pci_dev *nv_dev = NULL;
	struct pci_dev *amd_dev = NULL;
	struct pci_dev *target;
	acpi_handle handle;
	struct acpi_buffer buf = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	int ret = -ENODATA;

	while ((pdev = pci_get_device(PCI_ANY_ID, PCI_ANY_ID, pdev)) != NULL) {
		u32 base_cls = pdev->class >> 16;
		u32 sub_cls = pdev->class >> 8;

		if (base_cls != PCI_BASE_CLASS_DISPLAY)
			continue;

		if (pdev->vendor == 0x10de &&
		    (sub_cls == PCI_CLASS_DISPLAY_VGA || sub_cls == PCI_CLASS_DISPLAY_3D)) {
			if (!nv_dev)
				nv_dev = pci_dev_get(pdev);
		} else if (pdev->vendor == 0x1002 && sub_cls == PCI_CLASS_DISPLAY_3D) {
			if (!amd_dev)
				amd_dev = pci_dev_get(pdev);
		}
	}

	target = nv_dev ? nv_dev : amd_dev;
	if (target) {
		handle = ACPI_HANDLE(&target->dev);
		if (handle) {
			status = acpi_get_name(handle, ACPI_FULL_PATHNAME, &buf);
			if (ACPI_SUCCESS(status) && buf.pointer) {
				strscpy(cached_dgpu_acpi_path, (const char *)buf.pointer,
					sizeof(cached_dgpu_acpi_path));
				if (out && out_len > 0)
					strscpy(out, cached_dgpu_acpi_path, out_len);
				ret = 0;
			}
			kfree(buf.pointer);
		}
	}

	pci_dev_put(nv_dev);
	pci_dev_put(amd_dev);

	if (ret != 0 && cached_dgpu_acpi_path[0] != '\0') {
		if (out && out_len > 0)
			strscpy(out, cached_dgpu_acpi_path, out_len);
		ret = 0;
	}

	return ret;
}

static ssize_t dgpu_acpi_path_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	char path[sizeof(cached_dgpu_acpi_path)];
	int ret;

	mutex_lock(&gigamate_acpi_mutex);
	ret = gigamate_lookup_dgpu_acpi_path_locked(path, sizeof(path));
	mutex_unlock(&gigamate_acpi_mutex);

	if (ret < 0)
		return ret;
	return sysfs_emit(buf, "%s\n", path);
}
static DEVICE_ATTR_RO(dgpu_acpi_path);

static ssize_t acpi_eval_show(struct device *dev,
			      struct device_attribute *attr, char *buf)
{
	char res[sizeof(last_eval_result)];
	bool valid;

	mutex_lock(&gigamate_acpi_mutex);
	valid = has_eval_result;
	if (valid)
		strscpy(res, last_eval_result, sizeof(res));
	mutex_unlock(&gigamate_acpi_mutex);

	if (!valid)
		return -ENODATA;
	return sysfs_emit(buf, "%s\n", res);
}

static bool gigamate_is_aero_x16_ec_dsmd(void)
{
	const char *vendor = dmi_get_system_info(DMI_SYS_VENDOR);
	const char *product = dmi_get_system_info(DMI_PRODUCT_NAME);
	acpi_handle dsmd_handle = NULL;

	if (!vendor || !product)
		return false;
	if (!strstr(vendor, "GIGABYTE") && !strstr(vendor, "Gigabyte"))
		return false;
	if (!strstr(product, "AERO X16"))
		return false;
	return ACPI_SUCCESS(acpi_get_handle(NULL, "\\_SB.PCI0.SBRG.EC0.DSMD", &dsmd_handle));
}

static void gigamate_recover_pg00_on_timeout(const char *path)
{
	size_t len = strlen(path);
	const char *suffix = ".PG00._ON";
	size_t slen = strlen(suffix);
	acpi_handle alib_handle = NULL;
	u8 ec_val = 0;

	if (len < slen || strcmp(path + len - slen, suffix) != 0)
		return;

	/*
	 * On verified AMD + NVIDIA Gigabyte AERO X16 laptops, PG00._ON calls
	 * \_SB.ALIB(0x18, 0) at entry and clears EC0.DSMD (EC 0x2D bit 0)
	 * plus calls \_SB.ALIB(0x18, 1) only after its While(NVID != 0x10DE)
	 * loop. If that loop hits AE_AML_LOOP_TIMEOUT, replay the EC0.DSMD
	 * clear only on verified AERO X16 hardware exposing EC0.DSMD, and call
	 * \_SB.ALIB(0x18, 1) if present so the EC and AMD SMU restore Dynamic Boost.
	 */
	if (gigamate_is_aero_x16_ec_dsmd() && ec_read(0x2D, &ec_val) == 0 && (ec_val & 0x01)) {
		u8 next_val = ec_val & ~0x01;
		if (ec_write(0x2D, next_val) == 0)
			pr_info(DRIVER_NAME ": PG00._ON timeout recovery: cleared EC0.DSMD (0x2D: 0x%02x -> 0x%02x)\n",
				ec_val, next_val);
	}

	if (ACPI_SUCCESS(acpi_get_handle(NULL, "\\_SB.ALIB", &alib_handle))) {
		union acpi_object alib_args[2];
		struct acpi_object_list arg_list = {
			.count = 2,
			.pointer = alib_args,
		};
		acpi_status alib_st;

		alib_args[0].type = ACPI_TYPE_INTEGER;
		alib_args[0].integer.value = 0x18;
		alib_args[1].type = ACPI_TYPE_INTEGER;
		alib_args[1].integer.value = 1;
		alib_st = acpi_evaluate_object(alib_handle, NULL, &arg_list, NULL);
		pr_info(DRIVER_NAME ": PG00._ON timeout recovery: \\_SB.ALIB(0x18, 1) status=0x%x\n",
			alib_st);
	}
}

static ssize_t acpi_eval_store(struct device *dev,
			       struct device_attribute *attr,
			       const char *buf, size_t count)
{
	char path[128];
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	acpi_status status;
	size_t start = 0, end = count, len, i;

	if (!capable(CAP_SYS_ADMIN))
		return -EPERM;

	while (start < end && isspace((unsigned char)buf[start]))
		start++;
	while (end > start && isspace((unsigned char)buf[end - 1]))
		end--;

	len = end - start;
	if (len < 2 || len >= sizeof(path))
		return -EINVAL;

	memcpy(path, buf + start, len);
	path[len] = '\0';

	if (path[0] != '\\')
		return -EINVAL;
	for (i = 0; i < len; i++) {
		unsigned char c = (unsigned char)path[i];
		if (!isalnum(c) && c != '_' && c != '.' && c != '\\')
			return -EINVAL;
	}

	mutex_lock(&gigamate_acpi_mutex);
	status = acpi_evaluate_object(NULL, path, NULL, &output);
	if (ACPI_FAILURE(status)) {
		if (status == AE_AML_LOOP_TIMEOUT)
			gigamate_recover_pg00_on_timeout(path);
		snprintf(last_eval_result, sizeof(last_eval_result), "error:0x%x", status);
		has_eval_result = true;
		kfree(output.pointer);
		mutex_unlock(&gigamate_acpi_mutex);
		pr_debug(DRIVER_NAME ": acpi_eval(%s) failed: 0x%x\n", path, status);
		return (status == AE_NOT_FOUND) ? -ENOENT : -EIO;
	}

	if (output.pointer) {
		union acpi_object *obj = output.pointer;
		if (obj->type == ACPI_TYPE_INTEGER) {
			snprintf(last_eval_result, sizeof(last_eval_result),
				 "0x%llx", (unsigned long long)obj->integer.value);
		} else if (obj->type == ACPI_TYPE_STRING && obj->string.pointer) {
			snprintf(last_eval_result, sizeof(last_eval_result),
				 "%.120s", obj->string.pointer);
		} else if (obj->type == ACPI_TYPE_BUFFER) {
			snprintf(last_eval_result, sizeof(last_eval_result),
				 "buffer:%u", obj->buffer.length);
		} else {
			strscpy(last_eval_result, "ok", sizeof(last_eval_result));
		}
		kfree(output.pointer);
	} else {
		strscpy(last_eval_result, "ok", sizeof(last_eval_result));
	}
	has_eval_result = true;
	mutex_unlock(&gigamate_acpi_mutex);

	return count;
}

static struct device_attribute dev_attr_acpi_eval = {
	.attr = { .name = "acpi_eval", .mode = 0644 },
	.show = acpi_eval_show,
	.store = acpi_eval_store,
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
	&dev_attr_usbc_adapter,
	&dev_attr_profile_writable,
	&dev_attr_charge_limit_writable,
	&dev_attr_npcf_atpp,
	&dev_attr_npcf_acbt,
	&dev_attr_npcf_amat,
	&dev_attr_npcf_dbac,
	&dev_attr_dgpu_acpi_path,
	&dev_attr_acpi_eval,
	NULL,
};

struct gigamate_opt_attr {
	bool (*supported)(void);
	struct device_attribute *dev_attr;
	bool *created;
};

static bool gigamate_has_cpu_stapm(void)    { return gigamate_wmbd_has_ecpt_mw_opcode(0xF1); }
static bool gigamate_has_cpu_slow_ppt(void) { return gigamate_wmbd_has_ecpt_mw_opcode(0xF2); }
static bool gigamate_has_cpu_fast_ppt(void) { return gigamate_wmbd_has_ecpt_mw_opcode(0xF3); }

static const struct gigamate_opt_attr gigamate_opt_attrs[] = {
	{ gigamate_has_cpu_stapm,      &dev_attr_cpu_stapm_w_writable,       &has_attr_cpu_stapm },
	{ gigamate_has_cpu_slow_ppt,   &dev_attr_cpu_slow_ppt_w_writable,    &has_attr_cpu_slow_ppt },
	{ gigamate_has_cpu_fast_ppt,   &dev_attr_cpu_fast_ppt_w_writable,    &has_attr_cpu_fast_ppt },
	{ gigamate_wmbd_has_ctgp_offset, &dev_attr_gpu_ctgp_offset_w_writable, &has_attr_gpu_ctgp_offset },
	{ gigamate_wmbd_has_dyn_boost, &dev_attr_gpu_dynamic_boost_writable, &has_attr_gpu_dynamic_boost },
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

	gigamate_cache_wmbd_aml();
	mutex_lock(&gigamate_acpi_mutex);
	gigamate_lookup_dgpu_acpi_path_locked(NULL, 0);
	mutex_unlock(&gigamate_acpi_mutex);

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

	ret = device_create_bin_file(&pdev->dev, &bin_attr_wmbd_aml);
	if (ret)
		pr_warn(DRIVER_NAME ": failed to create wmbd_aml sysfs bin file (%d)\n", ret);

	for (i = 0; i < ARRAY_SIZE(gigamate_opt_attrs); i++) {
		*gigamate_opt_attrs[i].created = false;
		if (!gigamate_opt_attrs[i].supported())
			continue;
		ret = device_create_file(&pdev->dev, gigamate_opt_attrs[i].dev_attr);
		if (ret == 0)
			*gigamate_opt_attrs[i].created = true;
		else
			pr_warn(DRIVER_NAME ": failed to create optional sysfs attr %s (%d)\n",
				gigamate_opt_attrs[i].dev_attr->attr.name, ret);
	}
	if (has_attr_cpu_stapm || has_attr_cpu_slow_ppt || has_attr_cpu_fast_ppt ||
	    has_attr_gpu_ctgp_offset || has_attr_gpu_dynamic_boost) {
		pr_info(DRIVER_NAME ": power-budget controls enabled (stapm=%d slow=%d fast=%d ctgp=%d db=%d)\n",
			has_attr_cpu_stapm, has_attr_cpu_slow_ppt, has_attr_cpu_fast_ppt,
			has_attr_gpu_ctgp_offset, has_attr_gpu_dynamic_boost);
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
	int i;

	for (i = 0; i < ARRAY_SIZE(gigamate_opt_attrs); i++) {
		if (*gigamate_opt_attrs[i].created) {
			device_remove_file(&pdev->dev, gigamate_opt_attrs[i].dev_attr);
			*gigamate_opt_attrs[i].created = false;
		}
	}
	device_remove_bin_file(&pdev->dev, &bin_attr_wmbd_aml);
	for (attr = gigamate_acpi_dev_attrs; *attr; attr++)
		device_remove_file(&pdev->dev, *attr);

	amw0_handle = NULL;
	wmbd_aml_len = 0;
	current_profile = -1;
	current_charge_limit = -1;
	current_cpu_stapm_w = -1;
	current_cpu_slow_ppt_w = -1;
	current_cpu_fast_ppt_w = -1;
	cached_dgpu_acpi_path[0] = '\0';
	last_eval_result[0] = '\0';
	has_eval_result = false;
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
