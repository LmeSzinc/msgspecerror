"""
MessagePack binary walker for finding and fixing invalid UTF-8 in string fields.

Provides two strategies:
- ``fixup_msgpack_unicode_fast`` -- target a single bad string using
  ``UnicodeDecodeError`` metadata (O(N) search, one fix).
- ``fixup_msgpack_unicode_slow`` -- walk the entire msgpack structure and
  fix every string with invalid UTF-8 (O(N) one-pass).
"""

from __future__ import annotations

import struct
from typing import Literal, Optional

# ── pre-compiled structs ─────────────────────────────────────────────────

_U16BE = struct.Struct('>H')
_U32BE = struct.Struct('>I')

_utf8_error_values = Literal['strict', 'replace', 'ignore', 'surrogateescape']


# ── helpers ──────────────────────────────────────────────────────────────

def _str_header_len(n: int) -> int:
    """Byte length of the msgpack str header for a payload of *n* bytes."""
    if n <= 31:
        return 1          # fixstr
    if n < 256:
        return 2          # str8
    if n < 65536:
        return 3          # str16
    return 5               # str32


def _make_str_header(n: int) -> bytes:
    """Build the msgpack str header bytes for a payload of *n* bytes."""
    if n <= 31:
        return bytes([0xa0 | n])
    if n < 256:
        return bytes([0xd9, n])
    if n < 65536:
        return b'\xda' + _U16BE.pack(n)
    return b'\xdb' + _U32BE.pack(n)


# ── header detection (fast method) ───────────────────────────────────────

def _check_str_header_at(data: bytes, payload_start: int, str_len: int
                         ) -> Optional[bytes]:
    """
    Check whether the bytes immediately before *payload_start* form a valid
    msgpack string header for a string of length *str_len*.

    Returns the header bytes when valid, ``None`` otherwise.
    """
    # fixstr (0xa0-0xbf)
    if payload_start >= 1:
        h = data[payload_start - 1]
        if 0xa0 <= h <= 0xbf and (h & 0x1f) == str_len:
            return bytes([h])

    # str8  (0xd9 + 1-byte length)
    if payload_start >= 2 and str_len < 256:
        h1 = data[payload_start - 2]
        h2 = data[payload_start - 1]
        if h1 == 0xd9 and h2 == str_len:
            return bytes([0xd9, str_len])

    # str16 (0xda + 2-byte big-endian length)
    if payload_start >= 3 and str_len < 65536:
        if data[payload_start - 3] == 0xda:
            actual_len = _U16BE.unpack_from(data, payload_start - 2)[0]
            if actual_len == str_len:
                return data[payload_start - 3:payload_start]

    # str32 (0xdb + 4-byte big-endian length)
    if payload_start >= 5:
        if data[payload_start - 5] == 0xdb:
            actual_len = _U32BE.unpack_from(data, payload_start - 4)[0]
            if actual_len == str_len:
                return data[payload_start - 5:payload_start]

    return None


# ── slow-walker helpers ─────────────────────────────────────────────────

def _str_header_len_peek(ba: bytearray, start: int) -> tuple[int, int]:
    """
    Read a msgpack str header at *start* and return ``(header_len, str_len)``.
    """
    op = ba[start]
    if 0xa0 <= op <= 0xbf:                     # fixstr
        return 1, op & 0x1f
    if op == 0xd9:                              # str8
        return 2, ba[start + 1]
    if op == 0xda:                              # str16
        return 3, _U16BE.unpack_from(ba, start + 1)[0]
    if op == 0xdb:                              # str32
        return 5, _U32BE.unpack_from(ba, start + 1)[0]
    raise ValueError(f"Not a str header at offset {start}")


def _try_fix_string(ba: bytearray, header_len: int, str_len: int,
                    item_start: int, utf8_error: str) -> int:
    """
    Try to decode the string at *item_start*.  If it contains invalid UTF-8
    the payload is replaced with the ``utf8_error``-decoded version and the
    header is updated if necessary.

    Returns the byte position immediately after this item.
    """
    payload_start = item_start + header_len
    payload = bytes(ba[payload_start:payload_start + str_len])
    try:
        payload.decode('utf-8')
    except UnicodeDecodeError:
        fixed = payload.decode('utf-8', utf8_error).encode('utf-8', utf8_error)
        new_strlen = len(fixed)
        new_header_len = _str_header_len(new_strlen)
        new_header = _make_str_header(new_strlen)
        old_total = header_len + str_len
        new_total = new_header_len + new_strlen
        ba[item_start:item_start + old_total] = new_header + fixed
        return item_start + new_total
    return item_start + header_len + str_len


def _walk_fix(ba: bytearray, start: int, utf8_error: str) -> int:
    """
    Walk one msgpack item starting at *start*, fixing any string with invalid
    UTF-8 encountered along the way.  Returns the byte position immediately
    after this item.
    """
    if start >= len(ba):
        return start

    op = ba[start]

    # ── positive fixint 0x00 .. 0x7f ──
    if op <= 0x7f:
        return start + 1

    # ── fixmap 0x80 .. 0x8f ──
    if 0x80 <= op <= 0x8f:
        n = op & 0x0f
        pos = start + 1
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)   # key
            pos = _walk_fix(ba, pos, utf8_error)   # value
        return pos

    # ── fixarray 0x90 .. 0x9f ──
    if 0x90 <= op <= 0x9f:
        n = op & 0x0f
        pos = start + 1
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)
        return pos

    # ── fixstr 0xa0 .. 0xbf ──
    if 0xa0 <= op <= 0xbf:
        return _try_fix_string(ba, 1, op & 0x1f, start, utf8_error)

    # ── nil 0xc0 ──
    if op == 0xc0:
        return start + 1

    # ── (never used) 0xc1 ──
    if op == 0xc1:
        return start + 1

    # ── false 0xc2 / true 0xc3 ──
    if op in (0xc2, 0xc3):
        return start + 1

    # ── bin8 0xc4 ──
    if op == 0xc4:
        return start + 2 + ba[start + 1]

    # ── bin16 0xc5 ──
    if op == 0xc5:
        blen = _U16BE.unpack_from(ba, start + 1)[0]
        return start + 3 + blen

    # ── bin32 0xc6 ──
    if op == 0xc6:
        blen = _U32BE.unpack_from(ba, start + 1)[0]
        return start + 5 + blen

    # ── ext8 0xc7 ──
    if op == 0xc7:
        elen = ba[start + 1]
        return start + 3 + elen        # op(1) + len(1) + type(1) + data(elen)

    # ── ext16 0xc8 ──
    if op == 0xc8:
        elen = _U16BE.unpack_from(ba, start + 1)[0]
        return start + 4 + elen        # op(1) + len(2) + type(1) + data(elen)

    # ── ext32 0xc9 ──
    if op == 0xc9:
        elen = _U32BE.unpack_from(ba, start + 1)[0]
        return start + 6 + elen        # op(1) + len(4) + type(1) + data(elen)

    # ── float32 0xca ──
    if op == 0xca:
        return start + 5

    # ── float64 0xcb ──
    if op == 0xcb:
        return start + 9

    # ── uint8 0xcc ──
    if op == 0xcc:
        return start + 2

    # ── uint16 0xcd ──
    if op == 0xcd:
        return start + 3

    # ── uint32 0xce ──
    if op == 0xce:
        return start + 5

    # ── uint64 0xcf ──
    if op == 0xcf:
        return start + 9

    # ── int8 0xd0 ──
    if op == 0xd0:
        return start + 2

    # ── int16 0xd1 ──
    if op == 0xd1:
        return start + 3

    # ── int32 0xd2 ──
    if op == 0xd2:
        return start + 5

    # ── int64 0xd3 ──
    if op == 0xd3:
        return start + 9

    # ── fixext1 0xd4 ──
    if op == 0xd4:
        return start + 3     # op(1) + type(1) + data(1)

    # ── fixext2 0xd5 ──
    if op == 0xd5:
        return start + 4     # op(1) + type(1) + data(2)

    # ── fixext4 0xd6 ──
    if op == 0xd6:
        return start + 6     # op(1) + type(1) + data(4)

    # ── fixext8 0xd7 ──
    if op == 0xd7:
        return start + 10    # op(1) + type(1) + data(8)

    # ── fixext16 0xd8 ──
    if op == 0xd8:
        return start + 18    # op(1) + type(1) + data(16)

    # ── str8 0xd9 ──
    if op == 0xd9:
        return _try_fix_string(ba, 2, ba[start + 1], start, utf8_error)

    # ── str16 0xda ──
    if op == 0xda:
        slen = _U16BE.unpack_from(ba, start + 1)[0]
        return _try_fix_string(ba, 3, slen, start, utf8_error)

    # ── str32 0xdb ──
    if op == 0xdb:
        slen = _U32BE.unpack_from(ba, start + 1)[0]
        return _try_fix_string(ba, 5, slen, start, utf8_error)

    # ── array16 0xdc ──
    if op == 0xdc:
        n = _U16BE.unpack_from(ba, start + 1)[0]
        pos = start + 3
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)
        return pos

    # ── array32 0xdd ──
    if op == 0xdd:
        n = _U32BE.unpack_from(ba, start + 1)[0]
        pos = start + 5
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)
        return pos

    # ── map16 0xde ──
    if op == 0xde:
        n = _U16BE.unpack_from(ba, start + 1)[0]
        pos = start + 3
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)   # key
            pos = _walk_fix(ba, pos, utf8_error)   # value
        return pos

    # ── map32 0xdf ──
    if op == 0xdf:
        n = _U32BE.unpack_from(ba, start + 1)[0]
        pos = start + 5
        for _ in range(n):
            pos = _walk_fix(ba, pos, utf8_error)   # key
            pos = _walk_fix(ba, pos, utf8_error)   # value
        return pos

    # ── negative fixint 0xe0 .. 0xff ──
    if op >= 0xe0:
        return start + 1

    # Unknown opcode – skip one byte so the walker doesn't hang
    return start + 1


# ── fast method ─────────────────────────────────────────────────────────

def fixup_msgpack_unicode_fast(
        data: bytes,
        error: UnicodeDecodeError,
        utf8_error: _utf8_error_values = 'replace',
) -> Optional[bytes]:
    """
    Locate the exact msgpack string that caused *error* and fix it with
    the given *utf8_error* handler.

    The function searches the raw msgpack buffer for the failing string's
    payload (``error.object``) and validates that the preceding bytes form a
    valid msgpack string header.  If exactly **one** candidate is found the
    payload is repaired in place and the fixed buffer is returned.

    Parameters
    ----------
    data:
        The original msgpack buffer.
    error:
        The ``UnicodeDecodeError`` raised during decoding.
    utf8_error:
        Error handling scheme for the string fix.  ``'replace'`` substitutes
        U+FFFD, ``'ignore'`` drops invalid bytes, ``'surrogateescape'``
        preserves surrogates, ``'strict'`` re-raises (not useful here).

    Returns
    -------
    The fixed ``bytes`` on success, ``None`` when the failing string cannot
    be unambiguously located (multiple candidates or no match).
    """
    payload: bytes = error.object
    str_len = len(payload)

    # Collect all (position, header) candidates by searching for the payload
    # and checking the bytes before it.
    candidates: list[tuple[int, bytes]] = []
    pos = 0
    while True:
        pos = data.find(payload, pos)
        if pos == -1:
            break
        hdr = _check_str_header_at(data, pos, str_len)
        if hdr is not None:
            candidates.append((pos, hdr))
        pos += 1

    if len(candidates) != 1:
        # Ambiguous – let the caller fall back to the slow walker.
        return None

    payload_start, header = candidates[0]
    header_len = len(header)

    # Decode with the requested error handler and re-encode
    fixed_payload = payload.decode('utf-8', utf8_error).encode('utf-8', utf8_error)
    new_strlen = len(fixed_payload)
    new_header_len = _str_header_len(new_strlen)
    new_header = _make_str_header(new_strlen)

    old_total = header_len + str_len
    new_total = new_header_len + new_strlen

    ba = bytearray(data)
    ba[payload_start - header_len:payload_start - header_len + old_total] = \
        new_header + fixed_payload
    return bytes(ba)


# ── slow method ─────────────────────────────────────────────────────────

def fixup_msgpack_unicode_slow(
        data: bytes,
        utf8_error: _utf8_error_values = 'replace',
) -> bytes:
    """
    Walk the entire msgpack structure, find every string whose payload is not
    valid UTF-8, and fix with the given *utf8_error* handler.

    This is a **one-pass O(N)** walk — the entire buffer is traversed once
    regardless of how many strings are repaired.  Binary segments (``bin``,
    ``ext``) are skipped, so opaque payloads are never touched.

    Parameters
    ----------
    data:
        The msgpack buffer to repair.
    utf8_error:
        Error handling scheme passed to ``bytes.decode()`` when a string is
        not valid UTF-8.  ``'replace'`` (default) substitutes U+FFFD,
        ``'ignore'`` drops invalid bytes, ``'surrogateescape'`` preserves
        surrogates.  ``'strict'`` would re-raise and is not useful here.

    Returns
    -------
    The fully repaired msgpack bytes.
    """
    ba = bytearray(data)
    if ba:
        _walk_fix(ba, 0, utf8_error)
    return bytes(ba)
