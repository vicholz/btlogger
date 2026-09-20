# nRF52840 dongle firmware

The Nordic PCA10059 dongle does **not** show up as a normal Bluetooth adapter. This Zephyr observer firmware turns it into a USB serial scanner: every advertisement is one JSON line.

## What you should see after flashing

```
{"v":1,"t":"hello","fw":"btlogger-observer","ver":"1.0.0"}
{"v":1,"t":"scan","state":"on"}
{"v":1,"t":"adv","ms":1234,"addr":"AA:BB:CC:DD:EE:FF","at":"random","rssi":-67,"evt":0,"adv":"020106..."}
```

The stock firmware on many dongles is `nRF52 USB CDC BLE Demo`. That is **not** a scanner. Flash this app (or use the host's BlueZ adapter via `SCANNER_BACKEND=bleak`).

## Build

You need [Zephyr](https://docs.zephyrproject.org/latest/develop/getting_started/index.html) and the SDK.

```bash
west build -p auto -b nrf52840dongle/nrf52840 firmware/observer
```

Older Zephyr board names: `nrf52840dongle_nrf52840`.

The hex is `build/zephyr/zephyr.hex`.

## Flash over USB DFU (no debugger)

1. Unplug the dongle.
2. Press and hold the side reset button.
3. Plug it in while holding the button. The red LED should pulse (bootloader).
4. Package and flash:

```bash
nrfutil pkg generate --hw-version 52 --sd-req 0x00 \
  --application build/zephyr/zephyr.hex --application-version 1 observer.zip

nrfutil dfu usb-serial -pkg observer.zip -p /dev/ttyACM0
```

Newer nRF Util:

```bash
nrfutil nrf5sdk-tools pkg generate --hw-version 52 --sd-req 0x00 \
  --application build/zephyr/zephyr.hex --application-version 1 observer.zip

nrfutil nrf5sdk-tools dfu usb-serial -pkg observer.zip -p /dev/ttyACM0
```

On Linux the DFU port is usually `/dev/ttyACM0`. After reset it comes back as the same CDC ACM device, now printing NDJSON.

## Without flashing

Set `SCANNER_BACKEND=bleak` and pass the host Bluetooth adapter into the scanner container. The Intel AX211 (or any BlueZ adapter) works immediately.
