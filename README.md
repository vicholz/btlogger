# btlogger

Passive BLE sighting logger for a Nordic **nRF52840 USB dongle** (PCA10059), with a Docker stack that stores every device it hears, looks up manufacturer and device type, and answers:

- What was seen in a time window
- When a device was last seen
- How **long** it was seen (this visit, last visit, total dwell, first→last span)
- Full packet / advertisement history
- Repeat visitors
- Hour-of-week patterns

## Architecture

```
nRF52840 dongle (observer firmware, USB CDC)
        or host BlueZ adapter (bleak)
                 │ NDJSON / HCI ads
                 ▼
            scanner ──► TimescaleDB
                 ▲           │
                 │           ├── FastAPI + dashboard  (:8080)
                 │           └── Grafana              (:3000)
```

The dongle is **not** a standard Bluetooth adapter until you flash firmware. Two ways to get advertisements:

| Backend | When to use |
|---|---|
| `serial` | After flashing `firmware/observer` — dedicated radio, Docker-friendly serial |
| `bleak` | Any BlueZ adapter (this machine already has an Intel AX211) — works immediately |
| `replay` | Demo data with a week of patterned history |
| `auto` (default) | Serial if observer firmware is talking, else BlueZ, else replay |

## Visualization: custom UI + Grafana

The investigative questions (look up a MAC, read the packet, see visit lengths) are a poor fit for Grafana alone, so the primary UI is a small dashboard served by the API at **http://localhost:8080**.

Grafana is included for time-series charts and printable-ish dashboards at **http://localhost:3000** (admin / `btlogger`). That is the right OSS tool for “sightings over time” and ops-style reports. The custom UI is the right tool for device history, dwell time, and packet decode.

## Quick start

```bash
cp .env.example .env   # already has lab defaults
docker compose up --build
```

Then open http://localhost:8088 (or `API_PORT` in `.env`; 8080 if free).

Demo mode (no radio required):

```bash
SCANNER_BACKEND=replay docker compose up --build
```

Use the built-in Bluetooth adapter without flashing the dongle:

```bash
SCANNER_BACKEND=bleak docker compose up --build
```

## How long a device has been seen

A **visit** is a continuous presence window. If nothing is heard from an address for `VISIT_GAP_SECONDS` (default 120s), the visit closes.

| Field | Meaning |
|---|---|
| This visit | Time since the current window started (device still in range, last heard &lt; 30s) |
| Last visit | Length of the most recently closed window |
| Total time seen | Sum of all visit durations |
| First → last | Calendar span from first sighting to last sighting (not dwell) |
| Visits | How many separate presence windows |

In-range is “heard in the last 30 seconds” (`IN_RANGE_SECONDS`). BLE advertisements are public broadcasts; random addresses rotate, so one phone can appear as many MACs.

## nRF52840 dongle

See [firmware/README.md](firmware/README.md). Short version:

1. Build Zephyr app `firmware/observer` for `nrf52840dongle/nrf52840`
2. Enter USB DFU (hold reset, plug in — red LED pulses)
3. `nrfutil dfu usb-serial -pkg observer.zip -p /dev/ttyACM0`

The scanner then reads `/dev/ttyACM0` as NDJSON.

Your dongle currently enumerates as `nRF52 USB CDC BLE Demo`. That demo is not a scanner; flash the observer firmware or use `SCANNER_BACKEND=bleak`.

## API

| Endpoint | Use |
|---|---|
| `GET /api/devices?q=&order=dwell` | Search / sort (dwell, visits, last_seen) |
| `GET /api/devices/{mac}` | Last seen, dwell, type, last packet |
| `GET /api/devices/{mac}/history` | Sighting + payload history |
| `GET /api/devices/{mac}/visits` | Presence windows and durations |
| `GET /api/devices/{mac}/pattern` | Hour × weekday heatmap |
| `GET /api/seen?since=&until=` | Devices heard in a window, with span in that window |
| `GET /api/repeats` | Come-back devices |
| `GET /api/report?since=&until=` | Summary report |
| `GET /api/report/distance?n=&tx_power=&bins=&present=` | Bucket devices by approximate RSSI range |

## Data kept

TimescaleDB hypertables for `sightings` and `visits`. Raw packets are retained 90 days and compressed after 7. Advertisements from the same device are coalesced to one stored sighting per second (`SIGHTING_MIN_INTERVAL_MS`).

Manufacturer names come from the Bluetooth SIG company identifier list (vendored from [Nordic's bluetooth-numbers-database](https://github.com/NordicSemiconductor/bluetooth-numbers-database)). Device type is inferred from manufacturer data (iBeacon, Find My, Fast Pair, Eddystone, …), GAP appearance, and 16-bit service UUIDs.
