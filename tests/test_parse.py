from core.classify import classify
from core.distance import bucket_for, estimate_distance_m, format_distance, parse_bins
from core.parse import parse_ad
from core.timeutil import format_duration, is_present
from datetime import datetime, timedelta, timezone


def test_ibeacon():
    payload = bytes.fromhex(
        "0201061aff4c000215fda50693a4e24fb1afcfc6eb0764782527d10102c5"
    )
    parsed = parse_ad(payload)
    info = classify(parsed, "AA:BB:CC:DD:EE:FF", "random")
    assert parsed["manufacturer_id"] == 0x004C
    assert parsed["manufacturer_name"] == "Apple, Inc."
    assert info["device_type"] == "iBeacon"
    assert info["extra"]["ibeacon_major"] == 0x27D1
    assert info["stable_id"].startswith("ibeacon:")


def test_eddystone_uid():
    frame = bytes.fromhex("00e5") + bytes(range(16))  # UID: tx + 10-byte ns + 6-byte instance
    payload = (
        bytes.fromhex("0201060302aafe")
        + bytes([3 + len(frame), 0x16])
        + bytes.fromhex("aafe")
        + frame
    )
    parsed = parse_ad(payload)
    info = classify(parsed)
    assert "feaa" in parsed["uuids"]
    assert info["device_type"] == "Eddystone-UID beacon"
    assert info["stable_id"].startswith("eddystone:")


def test_local_name_and_heart_rate():
    payload = bytes.fromhex("02010603020d1809094368617267652035")
    parsed = parse_ad(payload)
    info = classify(parsed, "C8:5C:A2:00:10:02", "public")
    assert parsed["name"] == "Charge 5"
    assert info["device_type"] == "Heart rate monitor"
    assert info["stable_id"].startswith("pub:")


def test_find_my():
    mfg = bytes([0x12, 0x19]) + bytes(range(22))
    payload = bytes.fromhex("020106") + bytes([len(mfg) + 1, 0xFF, 0x4C, 0x00]) + mfg
    info = classify(parse_ad(payload))
    assert "Find My" in info["device_type"]


def test_duration_format():
    assert format_duration(0) == "0s"
    assert format_duration(75) == "1m 15s"
    assert format_duration(3725) == "1h 2m 5s"


def test_distance_at_one_meter():
    assert abs(estimate_distance_m(-59, -59, 2.7) - 1.0) < 0.01
    assert estimate_distance_m(-50, -59, 2.7) < 1
    assert estimate_distance_m(-80, -59, 2.7) > 5
    assert format_distance(0.3) == "< 0.5 m"
    assert parse_bins("1, 8, 3") == [1.0, 3.0, 8.0]
    assert bucket_for(0.4, [1, 3, 8]) == 0
    assert bucket_for(2.0, [1, 3, 8]) == 1
    assert bucket_for(20.0, [1, 3, 8]) == 3


def test_present_window():
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    last = now - timedelta(seconds=10)
    assert is_present(last, now, 30)
    assert not is_present(last, now, 5)


if __name__ == "__main__":
    tests = [
        test_ibeacon,
        test_eddystone_uid,
        test_local_name_and_heart_rate,
        test_find_my,
        test_duration_format,
        test_distance_at_one_meter,
        test_present_window,
    ]
    for fn in tests:
        fn()
        print("ok", fn.__name__)
    print("all passed")
