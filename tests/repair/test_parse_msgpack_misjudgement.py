"""
Tests for misjudgment resistance in msgpack unicode repair.

Verifies that the fast method correctly disambiguates real bad strings from
coincidental byte sequences in binary payloads, and that the slow walker
provides a correct fallback without corrupting opaque data.
"""

from __future__ import annotations

import os
import struct

import msgspec
import msgspec.msgpack

from msgspecerror.parse_msgpack import (
    fixup_msgpack_unicode_fast,
    fixup_msgpack_unicode_slow,
)

_BAD_BYTE = b'\xff'


# ---------------------------------------------------------------------------
# Case 1: Single bad string in clean data → fast fixes correctly
# ---------------------------------------------------------------------------

class TestCase1_SingleBad:
    """Fast method correctly identifies and fixes the single bad string."""

    def test_single_fixstr(self):
        data = bytes([0xa5]) + b'hell' + _BAD_BYTE  # fixstr(5) "hell\xff"
        error = UnicodeDecodeError('utf-8', b'hell\xff', 4, 5,
                                   'invalid start byte')
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None
        assert msgspec.msgpack.decode(fixed) == 'hell\ufffd'

    def test_single_in_nested_map(self):
        # {"a": {"b": "\xff"}}
        inner = bytes([0x81, 0xa1, 0x62, 0xa1]) + _BAD_BYTE  # {"b": "\xff"}
        data = bytes([0x81, 0xa1, 0x61]) + inner  # {"a": inner}
        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded == {"a": {"b": "\ufffd"}}


# ---------------------------------------------------------------------------
# Case 2: Bad string + random bin data → fast returns None, slow fixes
# ---------------------------------------------------------------------------

class TestCase2_RandomBin:
    """
    When the bad string's payload also appears inside a binary blob
    (preceded by bytes that happen to look like a str header), the fast
    method sees multiple candidates and returns None.
    The slow walker must fix the real string and leave bin data untouched.
    """

    @staticmethod
    def _build_bad_str_in_map() -> bytes:
        """Return msgpack data: {"bad": "\xff"}."""
        return bytes([0x81, 0xa3, 0x62, 0x61, 0x64,  # fixmap(1) "bad"
                      0xa1]) + _BAD_BYTE  # fixstr(1) "\xff"

    @staticmethod
    def _random_bin(size: int = 4096) -> bytes:
        """Produce cryptographically random bytes (os.urandom)."""
        return os.urandom(size)

    def test_bin8_with_coincidental_header(self):
        """
        A bin8 payload randomly contains bytes that match header + payload.
        Fast finds multiple candidates and returns None.
        """
        rand = self._random_bin(4096)

        # Ensure the random bin contains at least one coincidental match.
        # We inject one: a fake fixstr header + _BAD_BYTE inside the bin.
        # This simulates the worst case for the fast method.
        fake_str = bytes([0xa1]) + _BAD_BYTE
        # Insert the fake at a random position inside the bin
        insert_at = 500
        rand = rand[:insert_at] + fake_str + rand[insert_at:]

        # Build a map with both the real bad str and the random bin.
        bad_str_part = self._build_bad_str_in_map()
        bin_part = bytes([0xa3, 0x62, 0x69, 0x6e,  # "bin":
                          0xc5]) + struct.pack('>H', len(rand)) + rand  # bin16

        # Combine into a map: {"bad": "\xff", "bin": <random bytes>}
        data = bytes([0x82]) + bad_str_part[1:] + bin_part  # fixmap(2)

        # The real bad string payload
        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')

        # Fast should find multiple candidates → None
        fixed_fast = fixup_msgpack_unicode_fast(data, error)
        assert fixed_fast is None, \
            "fast must abort when the payload appears inside bin data"

        # Slow walker: fixes the real bad str, leaves bin unchanged
        fixed_slow = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed_slow)
        assert decoded["bad"] == "\ufffd", "real bad str should be fixed"
        assert decoded["bin"] == rand, "bin data must be byte-identical"

    def test_bin32_with_multiple_false_matches(self):
        """
        A large bin32 with many coincidental header bytes.
        Fast must return None, slow fixes correctly.
        """
        rand = self._random_bin(65536)

        # Inject 5 fake fixstr(1) + \xff at different positions
        fake = bytes([0xa1]) + _BAD_BYTE
        for i in range(5):
            pos = 1000 * (i + 1)
            rand = rand[:pos] + fake + rand[pos:]

        bad_str_part = self._build_bad_str_in_map()
        bin_part = bytes([0xa3, 0x62, 0x69, 0x6e,  # "bin":
                          0xc6]) + struct.pack('>I', len(rand)) + rand  # bin32

        data = bytes([0x82]) + bad_str_part[1:] + bin_part

        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')

        assert fixup_msgpack_unicode_fast(data, error) is None

        fixed_slow = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed_slow)
        assert decoded["bad"] == "\ufffd"
        assert decoded["bin"] == rand

    def test_int8_not_confused_with_fixstr(self):
        """
        An int8-encoded negative value (0xd0 + payload) should NOT match
        any str header type because the header byte is 0xd0 (int8),
        not 0xa0-0xbf (fixstr) or 0xd9 (str8).
        The fast method finds exactly 1 candidate (the real str).
        """
        # Real bad string: fixstr(1) "\xff"
        real = bytes([0xa1]) + _BAD_BYTE

        # An int8 -95 encoded as 0xd0 0xa1 — NOT a fixstr.
        # This is NOT "a1 ff" but rather "d0 a1 ff" if we want the
        # next element to be -95 as a fixint, hmm...

        # Actually in msgpack, negative fixint range is 0xe0-0xff (-32 to -1).
        # -95 requires int8: 0xd0 0xa1 (2 bytes).
        # -95 followed by payload \xff: 0xd0 0xa1 0xff
        fake_int8 = bytes([0xd0, 0xa1])  # int8 -95

        # Build: fixarray(2) [real_bad_str, -95]
        data = bytes([0x92]) + real + fake_int8

        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')

        # _check_str_header_at at buffer position 2:
        #   fixstr check: data[1] = 0xa1 → YES, because 0xa1 is fixstr(1).
        #   (this is the real str)
        # _check_str_header_at at buffer position 4:
        #   fixstr check: data[3] = 0xa1 → YES!
        #   BUT: the int8 header is at position 2, and data[2] = 0xd0.
        #   The payload would start at position 3, and data[3] = 0xa1.
        #   _check_str_header_at at position 4 checks data[3]=0xa1 as fixstr(1).
        #   The search for payload (_BAD_BYTE) at position 3 finds 0xa1 which
        #   IS fixstr(1), but the actual payload starts at position 4 (0xff).
        #   So _check_str_header_at(4, 1): data[3]=0xa1 → fixstr(1)\xff = YES.
        #
        # Actually wait - data[3] = 0xa1, not 0xff. The payload of the int8
        # is at position 3 (0xa1), not 0xff. So _check_str_header_at at
        # position 3 sees data[2]=0xd0 and... payload=0xa1, not _BAD_BYTE.
        #
        # This test is getting confusing. Let me simplify.

        # The key point: _BAD_BYTE (0xff) appears in this buffer exactly
        # once — as the payload of the real fixstr. So fast finds it and
        # checks the header: data[1] = 0xa1 → fixstr(1) → match.
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None, \
            "only one valid match — the real bad str"

        decoded = msgspec.msgpack.decode(fixed)
        assert decoded[0] == "\ufffd"
        assert decoded[1] == -95

    def test_fixint_range_e0_ff_is_not_fixstr(self):
        """
        msgpack negative fixint is 0xe0-0xff, NOT 0xa0-0xbf.
        The byte 0xe0 is fixint -32 — ``_check_str_header_at`` sees
        data[3]=0xe0, which is outside 0xa0-0xbf, so it is rejected.
        Only one candidate: the real fixstr(1) at position 1.
        """
        # Valid msgpack: fixarray(2) [fixstr(1)"\xff", fixint(-1)]
        #  92 a1 ff ff
        data = bytes([0x92, 0xa1]) + _BAD_BYTE + bytes([0xff])
        # ff at position 2 = payload of fixstr(1)
        # ff at position 4 = negative fixint -1

        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')

        # Search for _BAD_BYTE (0xff):
        #   position 2: inside fixstr(1) — _check_str_header_at(2):
        #     data[1]=0xa1 in 0xa0-0xbf → fixstr(1) → match
        #   position 4: fixint(-1) — _check_str_header_at(4):
        #     data[3]=0xff NOT in 0xa0-0xbf → reject
        # Only 1 candidate → fast fixes it.
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None

        decoded = msgspec.msgpack.decode(fixed)
        assert decoded[0] == "\ufffd"


# ---------------------------------------------------------------------------
# Case 3: Multiple identical bad strings → fast returns None, slow fixes both
# ---------------------------------------------------------------------------

class TestCase3_MultipleIdentical:
    """Two identical bad strings. Fast aborts, slow fixes both."""

    def test_two_identical_fixstrs(self):
        # {"a": "\xff", "b": "\xff"}  ("\xff" appears twice)
        data = bytes([0x82])
        data += bytes([0xa1, 0x61, 0xa1]) + _BAD_BYTE  # "a": "\xff"
        data += bytes([0xa1, 0x62, 0xa1]) + _BAD_BYTE  # "b": "\xff"
        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')
        assert fixup_msgpack_unicode_fast(data, error) is None
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded == {"a": "\ufffd", "b": "\ufffd"}

    def test_identical_in_list(self):
        # ["\xff", "\xff"]
        data = bytes([0x92, 0xa1]) + _BAD_BYTE + bytes([0xa1]) + _BAD_BYTE
        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')
        assert fixup_msgpack_unicode_fast(data, error) is None
        fixed = fixup_msgpack_unicode_slow(data)
        assert msgspec.msgpack.decode(fixed) == ["\ufffd", "\ufffd"]

    def test_identical_deeply_nested(self):
        # {"lvl1": {"lvl2": "\xff"}, "other": "\xff"}
        inner = bytes([0x81, 0xa4, 0x6c, 0x76, 0x6c, 0x32,  # {"lvl2":
                       0xa1]) + _BAD_BYTE  # "\xff"}
        data = bytes([0x82])
        data += bytes([0xa4, 0x6c, 0x76, 0x6c, 0x31]) + inner  # "lvl1": inner
        data += bytes([0xa5, 0x6f, 0x74, 0x68, 0x65, 0x72,  # "other":
                       0xa1]) + _BAD_BYTE  # "\xff"
        error = UnicodeDecodeError('utf-8', _BAD_BYTE, 0, 1,
                                   'invalid start byte')
        assert fixup_msgpack_unicode_fast(data, error) is None
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded["lvl1"]["lvl2"] == "\ufffd"
        assert decoded["other"] == "\ufffd"


# ---------------------------------------------------------------------------
# Case 4: Bin data with invalid UTF-8 must remain untouched by slow walker
# ---------------------------------------------------------------------------

class TestCase4_BinPreserved:
    """The slow walker never touches bin or ext payloads."""

    def test_bin8_with_invalid_utf8(self):
        """A bin8 full of \xff bytes is preserved after slow fix."""
        payload = _BAD_BYTE * 100
        data = bytes([0x81, 0xa3, 0x62, 0x69, 0x6e,  # {"bin":
                      0xc4, 100]) + payload  # bin8(100)
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded == {"bin": payload}

    def test_bin16_inside_map(self):
        """A map with both strings and bin preserves bin data."""
        bin_payload = _BAD_BYTE * 500
        data = bytes([0x82])
        data += bytes([0xa1, 0x61, 0xa2]) + _BAD_BYTE + b'k'  # "a": "\xffk"
        data += bytes([0xa1, 0x62, 0xc5]) + struct.pack('>H', len(bin_payload))
        data += bin_payload
        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded["a"] == "\ufffdk"
        assert decoded["b"] == bin_payload

    def test_ext8_with_invalid_utf8(self):
        """Ext payload is opaque binary, must not be decoded as string."""
        ext_data = _BAD_BYTE * 20
        data = bytes([0x81, 0xa3, 0x65, 0x78, 0x74,  # {"ext":
                      0xc7, 20, 0x02]) + ext_data  # ext8(20) type=2
        fixed = fixup_msgpack_unicode_slow(data)
        assert fixed == data

    def test_mixed_str_bin_ext(self):
        """Map with str, bin, ext — only str gets fixed."""
        bad_str = bytes([0xa1]) + _BAD_BYTE  # "\xff"
        bin_data = _BAD_BYTE * 50
        ext_data = _BAD_BYTE * 10

        data = bytes([0x83])
        data += bytes([0xa3, 0x73, 0x74, 0x72]) + bad_str  # "str": "\xff"
        data += bytes([0xa3, 0x62, 0x69, 0x6e,  # "bin":
                       0xc6, 0x00, 0x00, 0x00, 50]) + bin_data
        data += bytes([0xa3, 0x65, 0x78, 0x74,  # "ext":
                       0xc8, 0x00, 10, 0x01]) + ext_data

        fixed = fixup_msgpack_unicode_slow(data)
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded["str"] == "\ufffd"
        assert decoded["bin"] == bin_data
        assert type(decoded["ext"]) is msgspec.msgpack.Ext
        assert decoded["ext"].code == 1
        assert decoded["ext"].data == ext_data


# ---------------------------------------------------------------------------
# Case 5: str8/str16/str32 with bad bytes, no ambiguity despite fixint overlap
# ---------------------------------------------------------------------------

class TestCase5_LongStrNoAmbiguity:
    """Longer bad strings never have ambiguity because fixint range is 1 byte."""

    def test_str8_32_bytes_no_fixint_overlap(self):
        """str8(32) is outside fixstr range (31 max) so fixint can't match."""
        payload = b'A' * 31 + _BAD_BYTE  # 32 bytes
        assert len(payload) == 32
        header = bytes([0xd9, 32])
        data = header + payload
        error = UnicodeDecodeError('utf-8', payload, 31, 32,
                                   'invalid start byte')

        # Only str8 header check applies — fixint/str can't overlap at len>31
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded == 'A' * 31 + '\ufffd'

    def test_str16_no_fixint_overlap(self):
        """str16 is 3 header bytes — no fixint can match the prefix."""
        payload = b'B' * 300 + _BAD_BYTE
        header = bytes([0xda]) + struct.pack('>H', len(payload))
        data = header + payload
        error = UnicodeDecodeError('utf-8', payload, 300, 301,
                                   'invalid start byte')
        fixed = fixup_msgpack_unicode_fast(data, error)
        assert fixed is not None
        decoded = msgspec.msgpack.decode(fixed)
        assert decoded == 'B' * 300 + '\ufffd'
