/*
 * btlogger observer: dump every BLE advertisement as one NDJSON line
 * on the USB CDC ACM console of the nRF52840 dongle.
 *
 * SPDX-License-Identifier: MIT
 */

#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

static const char *addr_type_str(uint8_t type)
{
	switch (type) {
	case BT_ADDR_LE_PUBLIC:
		return "public";
	case BT_ADDR_LE_RANDOM:
		return "random";
	default:
		return "unknown";
	}
}

static void hex_encode(char *out, size_t out_len, const uint8_t *data, uint8_t len)
{
	static const char hex[] = "0123456789abcdef";
	size_t max = (out_len - 1) / 2;
	uint8_t n = len < max ? len : (uint8_t)max;

	for (uint8_t i = 0; i < n; i++) {
		out[i * 2] = hex[(data[i] >> 4) & 0xF];
		out[i * 2 + 1] = hex[data[i] & 0xF];
	}
	out[n * 2] = '\0';
}

static void device_found(const bt_addr_le_t *addr, int8_t rssi, uint8_t type,
			 struct net_buf_simple *ad)
{
	char addr_str[BT_ADDR_LE_STR_LEN];
	char hex[512];

	bt_addr_le_to_str(addr, addr_str, sizeof(addr_str));

	/* bt_addr_le_to_str formats "AA:BB:... (random)"; keep only the MAC. */
	char *paren = strchr(addr_str, ' ');
	if (paren) {
		*paren = '\0';
	}

	hex_encode(hex, sizeof(hex), ad->data, ad->len);

	printk("{\"v\":1,\"t\":\"adv\",\"ms\":%lld,\"addr\":\"%s\",\"at\":\"%s\","
	       "\"rssi\":%d,\"evt\":%u,\"adv\":\"%s\"}\n",
	       (long long)k_uptime_get(), addr_str, addr_type_str(addr->type),
	       rssi, type, hex);
}

int main(void)
{
	int err;
	struct bt_le_scan_param scan_param = {
		.type = BT_LE_SCAN_TYPE_ACTIVE,
		.options = BT_LE_SCAN_OPT_NONE,
		.interval = BT_GAP_SCAN_FAST_INTERVAL,
		.window = BT_GAP_SCAN_FAST_WINDOW,
	};

	printk("{\"v\":1,\"t\":\"hello\",\"fw\":\"btlogger-observer\",\"ver\":\"1.0.0\"}\n");

	err = bt_enable(NULL);
	if (err) {
		printk("{\"v\":1,\"t\":\"error\",\"msg\":\"bt_enable\",\"err\":%d}\n", err);
		return 0;
	}

	err = bt_le_scan_start(&scan_param, device_found);
	if (err) {
		printk("{\"v\":1,\"t\":\"error\",\"msg\":\"scan_start\",\"err\":%d}\n", err);
		return 0;
	}

	printk("{\"v\":1,\"t\":\"scan\",\"state\":\"on\"}\n");

	while (1) {
		k_sleep(K_SECONDS(10));
		printk("{\"v\":1,\"t\":\"hb\",\"ms\":%lld}\n", (long long)k_uptime_get());
	}
}
