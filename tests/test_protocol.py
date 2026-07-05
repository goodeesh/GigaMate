import pytest
from gigabyte_keyboard_rgb.protocol import make_checksum, make_command, COLOURS


def test_red_command():
    """Known-working red static command."""
    cmd = make_command(0x01, 0x06, 0x64, 0x01)
    expected = bytes([0x08, 0x00, 0x01, 0x06, 0x64, 0x01, 0x01, 0x8A])
    assert cmd == expected, f"{cmd.hex()} != {expected.hex()}"


def test_green_command():
    cmd = make_command(0x01, 0x06, 0x64, 0x02)
    expected = bytes([0x08, 0x00, 0x01, 0x06, 0x64, 0x02, 0x01, 0x89])
    assert cmd == expected


def test_all_colours():
    """Checksum must be self-consistent for all static colour commands."""
    for cname, cval in COLOURS.items():
        cmd = make_command(0x01, 0x06, 0x64, cval)
        assert cmd[0] == 0x08
        assert cmd[2] == 0x01  # static
        assert cmd[-1] == make_checksum(list(cmd[:-1]) + [0])


def test_checksum_roundtrip():
    for b0 in range(0x00, 0x10):
        for b2 in range(0x01, 0x0E):
            for b3 in range(0x01, 0x0B):
                d = [0x08, b0, b2, b3, 0x64, 0x01, 0x01]
                cs = make_checksum(d)
                computed = (255 - sum(d[:7])) & 0xFF
                assert cs == computed
