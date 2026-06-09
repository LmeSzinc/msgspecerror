"""
Tests for the **slow** walker in ``msgspecerror.parse_msgpack``.

Covers ``_walk_fix`` with all 24 msgpack opcodes, container traversal,
ext/bin untouched, and ``fixup_msgpack_unicode_slow`` round-trips.
"""
import struct

import msgspec
import msgspec.msgpack
import pytest

from msgspecerror.parse_msgpack import (
    _make_str_header,
    _walk_fix,
    fixup_msgpack_unicode_slow,
)

# ── helpers ─────────────────────────────────────────────────────────────

_BAD_BYTE = b'\xff'

_REPLACEMENT_UTF8 = '\ufffd'.encode('utf-8')  # 3 bytes: ef bf bd


def _msgpack(obj) -> bytes:
    return msgspec.msgpack.encode(obj)


# ── slow walker: single items ──────────────────────────────────────────

class TestWalkFix:
    """Test the internal ``_walk_fix`` walker on individual opcodes."""

    def test_positive_fixint(self):
        ba = bytearray(b'\x7f')
        assert _walk_fix(ba, 0, 'replace') == 1

    def test_negative_fixint(self):
        ba = bytearray(b'\xe0')
        assert _walk_fix(ba, 0, 'replace') == 1

    def test_nil(self):
        ba = bytearray(b'\xc0')
        assert _walk_fix(ba, 0, 'replace') == 1

    def test_bool(self):
        ba = bytearray(b'\xc3')  # true
        assert _walk_fix(ba, 0, 'replace') == 1

    def test_uint8(self):
        ba = bytearray(b'\xcc\xff')
        assert _walk_fix(ba, 0, 'replace') == 2

    def test_int64(self):
        ba = bytearray(b'\xd3' + b'\x00' * 8)
        assert _walk_fix(ba, 0, 'replace') == 9

    def test_float32(self):
        ba = bytearray(b'\xca' + struct.pack('>f', 1.5))
        assert _walk_fix(ba, 0, 'replace') == 5

    def test_float64(self):
        ba = bytearray(b'\xcb' + struct.pack('>d', 1.5))
        assert _walk_fix(ba, 0, 'replace') == 9

    def test_bin8(self):
        ba = bytearray(b'\xc4\x05hello')
        assert _walk_fix(ba, 0, 'replace') == 7

    def test_bin16(self):
        ba = bytearray(b'\xc5\x00\x05hello')
        assert _walk_fix(ba, 0, 'replace') == 8

    def test_bin32(self):
        ba = bytearray(b'\xc6\x00\x00\x00\x05hello')
        assert _walk_fix(ba, 0, 'replace') == 10

    def test_fixext1(self):
        ba = bytearray(b'\xd4\x01\xff')
        assert _walk_fix(ba, 0, 'replace') == 3

    def test_fixext16(self):
        ba = bytearray(b'\xd8\x01' + b'\x00' * 16)
        assert _walk_fix(ba, 0, 'replace') == 18

    def test_ext8(self):
        ba = bytearray(b'\xc7\x05\x01' + b'hello')
        assert _walk_fix(ba, 0, 'replace') == 8

    def test_ext32(self):
        ba = bytearray(b'\xc9\x00\x00\x00\x05\x01' + b'hello')
        assert _walk_fix(ba, 0, 'replace') == 11


# ── slow walker: strings ───────────────────────────────────────────────

class TestWalkFixStrings:
    def test_fixstr_valid(self):
        ba = bytearray(b'\xa5hello')
        _walk_fix(ba, 0, 'replace')
        assert bytes(ba) == b'\xa5hello'

    def test_fixstr_bad(self):
        ba = bytearray(b'\xa1' + _BAD_BYTE)
        _walk_fix(ba, 0, 'replace')
        fixed = bytes(ba)
        assert fixed[0] == 0xa3  # fixstr length 3
        assert fixed[1:] == _REPLACEMENT_UTF8

    def test_str8_bad(self):
        payload = _BAD_BYTE + b'A'
        ba = bytearray(bytes([0xd9, len(payload)]) + payload)
        _walk_fix(ba, 0, 'replace')
        assert b'\xef\xbf\xbd' in ba  # replacement char present
        assert b'A' in ba

    def test_str16_bad(self):
        payload = _BAD_BYTE * 3 + b'valid'
        ba = bytearray(bytes([0xda]) + struct.pack('>H', len(payload)) + payload)
        _walk_fix(ba, 0, 'replace')
        assert msgspec.msgpack.decode(bytes(ba)) == '\ufffd\ufffd\ufffdvalid'

    def test_str32_bad(self):
        payload = _BAD_BYTE * 4 + b'tail'
        ba = bytearray(bytes([0xdb]) + struct.pack('>I', len(payload)) + payload)
        _walk_fix(ba, 0, 'replace')
        assert msgspec.msgpack.decode(bytes(ba)) == '\ufffd\ufffd\ufffd\ufffdtail'

    def test_utf8_error_ignore(self):
        """With ``utf8_error='ignore'`` bad bytes are removed."""
        payload = _BAD_BYTE + b'good' + _BAD_BYTE
        ba = bytearray(bytes([0xd9, len(payload)]) + payload)
        _walk_fix(ba, 0, 'ignore')
        assert msgspec.msgpack.decode(bytes(ba)) == 'good'


# ── slow walker: containers ───────────────────────────────────────────

class TestWalkFixContainers:
    def test_fixmap(self):
        data = bytes([0x81, 0xa1, 0x61, 0xa1]) + _BAD_BYTE  # {"a": "\xff"}
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == {"a": "\ufffd"}

    def test_fixarray_with_bad_string(self):
        data = bytes([0x91, 0xa1]) + _BAD_BYTE  # ["\xff"]
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == ["\ufffd"]

    def test_nested_map(self):
        # {"lvl1": {"lvl2": "\xff"}}
        data = bytes([0x81, 0xa4, 0x6c, 0x76, 0x6c, 0x31,  # fixmap(1) fixstr(4)"lvl1"
                      0x81, 0xa4, 0x6c, 0x76, 0x6c, 0x32,  # fixmap(1) fixstr(4)"lvl2"
                      0xa1]) + _BAD_BYTE  # fixstr(1)"\xff"
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == {"lvl1": {"lvl2": "\ufffd"}}

    def test_array_in_map(self):
        # {"key": ["ok", "\xff"]}
        data = bytes([0x81, 0xa3, 0x6b, 0x65, 0x79,  # fixmap(1) fixstr(3)"key"
                      0x92, 0xa2, 0x6f, 0x6b,        # fixarray(2) fixstr(2)"ok"
                      0xa1]) + _BAD_BYTE             # fixstr(1)"\xff"
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == {"key": ["ok", "\ufffd"]}

    def test_map_in_array(self):
        # [{"val": "\xff"}]
        data = bytes([0x91, 0x81, 0xa3, 0x76, 0x61, 0x6c,  # fixarray(1) fixmap(1) fixstr(3)"val"
                      0xa1]) + _BAD_BYTE                   # fixstr(1)"\xff"
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == [{"val": "\ufffd"}]

    def test_array16(self):
        items = bytes([0xa1]) + _BAD_BYTE + bytes([0xa1]) + _BAD_BYTE + bytes([0xa1]) + _BAD_BYTE
        data = bytes([0xdc, 0x00, 0x03]) + items
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == ["\ufffd", "\ufffd", "\ufffd"]

    def test_map16(self):
        data = bytes([0xde, 0x00, 0x01, 0xa3, 0x6b, 0x65, 0x79])  # map16(1) fixstr(3)"key"
        data += bytes([0xa1]) + _BAD_BYTE                           # fixstr(1)"\xff"
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == {"key": "\ufffd"}


# ── slow walker: ext data not touched ─────────────────────────────────

class TestWalkFixExtUntouched:
    """Ensure ext payloads (opaque binary) are never decoded as strings."""

    def test_ext8_untouched(self):
        contents = bytes([0x81, 0xa3, 0x65, 0x78, 0x74,  # {"ext": ...
                         0xc7, 0x0a, 0x02]) + _BAD_BYTE * 10
        assert fixup_msgpack_unicode_slow(contents) == contents

    def test_fixext1_untouched(self):
        data = bytes([0x81, 0xa3, 0x65, 0x78, 0x74,      # {"ext":
                      0xd4, 0x01]) + _BAD_BYTE             # fixext1(1)type=1...}
        assert fixup_msgpack_unicode_slow(data) == data

    def test_mixed_str_and_ext(self):
        data = bytes([0x82, 0xa3, 0x73, 0x74, 0x72,       # {"str":
                      0xa1]) + _BAD_BYTE                   # "\xff"
        data += bytes([0xa3, 0x65, 0x78, 0x74,             # ,"ext":
                       0xc7, 0x05, 0x01]) + _BAD_BYTE * 5  # ext8(5)type=1...}
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded["str"] == "\ufffd"
        assert type(decoded["ext"]) is msgspec.msgpack.Ext


# ── slow walker: binary not touched ───────────────────────────────────

class TestWalkFixBinUntouched:
    def test_bin8_untouched(self):
        data = bytes([0x81, 0xa3, 0x62, 0x69, 0x6e,  # {"bin":
                      0xc4, 0x05]) + _BAD_BYTE * 5    # bin8(5)...}
        assert fixup_msgpack_unicode_slow(data) == data

    def test_bin16_untouched(self):
        data = bytes([0x81, 0xa3, 0x62, 0x69, 0x6e,    # {"bin":
                      0xc5, 0x00, 0x0a]) + _BAD_BYTE * 10
        assert fixup_msgpack_unicode_slow(data) == data


# ── slow method: happy path ──────────────────────────────────────────

class TestSlowMethod:
    def test_valid_data_unchanged(self):
        data = _msgpack({"hello": "world", "nums": [1, 2, 3]})
        assert fixup_msgpack_unicode_slow(data) == data
        assert msgspec.msgpack.decode(fixup_msgpack_unicode_slow(data)) == \
            {"hello": "world", "nums": [1, 2, 3]}

    def test_multiple_bad_strings(self):
        data = bytes([0x82, 0xa1, 0x61, 0xa1]) + _BAD_BYTE   # "a": "\xff"
        data += bytes([0xa1, 0x62, 0xa2]) + _BAD_BYTE + b'c'  # "b": "\xffc"
        decoded = msgspec.msgpack.decode(fixup_msgpack_unicode_slow(data))
        assert decoded == {"a": "\ufffd", "b": "\ufffdc"}

    def test_empty_string(self):
        data = _msgpack({"empty": ""})
        assert fixup_msgpack_unicode_slow(data) == data

    def test_long_string(self):
        long_str = "x" * 100000
        data = _msgpack(long_str)
        assert fixup_msgpack_unicode_slow(data) == data

    def test_long_string_with_bad_bytes(self):
        payload = b'A' * 100 + _BAD_BYTE + b'B' * 100
        data = _make_str_header(len(payload)) + payload
        decoded = msgspec.msgpack.decode(fixup_msgpack_unicode_slow(data))
        assert decoded == 'A' * 100 + '\ufffd' + 'B' * 100

    def test_utf8_error_ignore_e2e(self):
        """End-to-end: ignore mode removes bad bytes from all strings."""
        data = bytes([0x82, 0xa1, 0x61, 0xa3]) + _BAD_BYTE + b'ok'  # "a": "\xffok" (3 bytes)
        data += bytes([0xa1, 0x62, 0xa3]) + b'ok' + _BAD_BYTE        # "b": "ok\xff" (3 bytes)
        decoded = msgspec.msgpack.decode(fixup_msgpack_unicode_slow(data, utf8_error='ignore'))
        assert decoded == {"a": "ok", "b": "ok"}


# ── round-trip ────────────────────────────────────────────────────────

class TestRoundTrip:
    """After slow fix, msgspec.encode(decode(fixed)) should be round-trip valid."""

    @pytest.mark.parametrize("payload", [
        _BAD_BYTE,
        _BAD_BYTE * 3,
        b'good' + _BAD_BYTE + b'more',
        b'\x80',
        b'\xfe\xff',
    ])
    def test_roundtrip(self, payload):
        data = _make_str_header(len(payload)) + payload
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        re_encoded = msgspec.msgpack.encode(decoded)
        assert msgspec.msgpack.decode(re_encoded) == decoded
