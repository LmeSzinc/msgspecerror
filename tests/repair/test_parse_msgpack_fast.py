"""
Tests for the **fast** method in ``msgspecerror.parse_msgpack``.

Covers header detection (``_check_str_header_at``), string header helpers,
and ``fixup_msgpack_unicode_fast`` with all four msgpack string codecs
(fixstr, str8, str16, str32).
"""
import struct

import msgspec.msgpack
import pytest

from msgspecerror.parse_msgpack import (
    _check_str_header_at,
    _make_str_header,
    _str_header_len,
    fixup_msgpack_unicode_fast,
)

# ── helpers ─────────────────────────────────────────────────────────────

_BAD_BYTE = b'\xff'

_REPLACEMENT_UTF8 = '\ufffd'.encode('utf-8')  # 3 bytes: ef bf bd


def _msgpack(obj) -> bytes:
    return msgspec.msgpack.encode(obj)


# ── _make_str_header / _str_header_len ────────────────────────────────

class TestMakeStrHeader:
    @pytest.mark.parametrize("n, expected_len", [
        (0, 1), (1, 1), (31, 1), (32, 2), (255, 2),
        (256, 3), (65535, 3), (65536, 5), (100000, 5),
    ])
    def test_header_len(self, n, expected_len):
        assert _str_header_len(n) == expected_len

    @pytest.mark.parametrize("n", [0, 1, 15, 31])
    def test_fixstr_header(self, n):
        h = _make_str_header(n)
        assert len(h) == 1
        assert 0xa0 <= h[0] <= 0xbf
        assert (h[0] & 0x1f) == n

    @pytest.mark.parametrize("n", [32, 100, 255])
    def test_str8_header(self, n):
        h = _make_str_header(n)
        assert len(h) == 2
        assert h[0] == 0xd9
        assert h[1] == n

    @pytest.mark.parametrize("n", [256, 1000, 65535])
    def test_str16_header(self, n):
        h = _make_str_header(n)
        assert len(h) == 3
        assert h[0] == 0xda
        assert struct.unpack('>H', h[1:])[0] == n

    @pytest.mark.parametrize("n", [65536, 100000, 2 ** 20])
    def test_str32_header(self, n):
        h = _make_str_header(n)
        assert len(h) == 5
        assert h[0] == 0xdb
        assert struct.unpack('>I', h[1:])[0] == n


# ── _check_str_header_at ──────────────────────────────────────────────

class TestCheckStrHeaderAt:
    def test_fixstr_detected(self):
        data = bytes([0xa5]) + b'hello'
        assert _check_str_header_at(data, 1, 5) == bytes([0xa5])

    def test_str8_detected(self):
        data = bytes([0xd9, 5]) + b'hello'
        assert _check_str_header_at(data, 2, 5) == bytes([0xd9, 5])

    def test_str16_detected(self):
        data = bytes([0xda, 0x00, 0x05]) + b'hello'
        assert _check_str_header_at(data, 3, 5) == bytes([0xda, 0x00, 0x05])

    def test_str32_detected(self):
        data = bytes([0xdb, 0x00, 0x00, 0x00, 0x05]) + b'hello'
        assert _check_str_header_at(data, 5, 5) == bytes([0xdb, 0x00, 0x00, 0x00, 0x05])

    def test_no_match_when_length_mismatch(self):
        data = bytes([0xa3]) + b'hello'
        assert _check_str_header_at(data, 1, 5) is None

    def test_no_match_on_fixint(self):
        data = bytes([0x05]) + b'hello'
        assert _check_str_header_at(data, 1, 5) is None

    def test_no_match_at_buffer_start(self):
        assert _check_str_header_at(b'hello', 0, 5) is None


# ── fast method ──────────────────────────────────────────────────────

class TestFastMethod:
    """Tests for ``fixup_msgpack_unicode_fast``."""

    @staticmethod
    def _bad_error(payload: bytes, pos: int = 0):
        return UnicodeDecodeError('utf-8', payload, pos, pos + 1,
                                  'invalid start byte')

    def test_fixstr_fix(self):
        payload = b'hell' + _BAD_BYTE
        data = bytes([0xa5]) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload, 4))
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == 'hell\ufffd'

    def test_str8_fix(self):
        payload = _BAD_BYTE + b' world'
        data = bytes([0xd9, len(payload)]) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload))
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == '\ufffd world'

    def test_str16_fix(self):
        payload = b'A' * 300 + _BAD_BYTE
        data = bytes([0xda]) + struct.pack('>H', len(payload)) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload, 300))
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == 'A' * 300 + '\ufffd'

    def test_str32_fix(self):
        payload = _BAD_BYTE + b'B' * 70000
        data = bytes([0xdb]) + struct.pack('>I', len(payload)) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload))
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == '\ufffd' + 'B' * 70000

    def test_ambiguous_returns_none(self):
        payload = b'valid'
        data = bytes([0xa5]) + payload + bytes([0xa5]) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload))
        assert fixed is None

    def test_no_match_fixstr_vs_fixint(self):
        payload = b'hello'
        data = b'\x05' + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload))
        assert fixed is None

    def test_nested_fast_fix(self):
        inner = bytes([0xa1]) + _BAD_BYTE
        data = bytes([0x81, 0xa3]) + b'val' + inner
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(_BAD_BYTE))
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == {"val": "\ufffd"}

    def test_utf8_error_ignore(self):
        """With ``utf8_error='ignore'`` the bad byte is removed entirely."""
        payload = b'hell' + _BAD_BYTE
        data = bytes([0xa5]) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload, 4), utf8_error='ignore')
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == 'hell'

    def test_utf8_error_surrogateescape(self):
        """``surrogateescape`` round-trips — original bytes preserved raw."""
        payload = b'hell' + _BAD_BYTE
        data = bytes([0xa5]) + payload
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(payload, 4), utf8_error='surrogateescape')
        assert fixed is not None
        # surrogateescape encode re-writes surrogates back to original bytes,
        # so the output still contains the raw \xff — the header+payload are
        # byte-identical to the original (with header size unchanged).
        assert bytes(fixed) == data  # round-trip preserves original bytes

    def test_multiple_candidates_returns_none(self):
        """Two identical bad strings — fast aborts, caller falls back to slow."""
        data = bytes([0xa1]) + _BAD_BYTE + bytes([0xa1]) + _BAD_BYTE
        fixed = fixup_msgpack_unicode_fast(data, self._bad_error(_BAD_BYTE))
        assert fixed is None


# ── fast → slow fallback ──────────────────────────────────────────────

def test_fast_falls_back_to_slow():
    """
    When the fast method can't pinpoint (ambiguous), it returns None.
    The caller should then use the slow walker.
    """
    data = bytes([0x82])
    data += bytes([0xa1, 0x61, 0xa1]) + _BAD_BYTE  # "a": "\xff" (fixstr len 1)
    data += bytes([0xa1, 0x62, 0xa1]) + _BAD_BYTE  # "b": "\xff" (fixstr len 1)
    from msgspecerror.parse_msgpack import fixup_msgpack_unicode_slow
    assert fixup_msgpack_unicode_fast(data, UnicodeDecodeError(
        'utf-8', _BAD_BYTE, 0, 1, 'invalid start byte')) is None
    fixed = fixup_msgpack_unicode_slow(data)
    assert msgspec.msgpack.decode(fixed) == {"a": "\ufffd", "b": "\ufffd"}
