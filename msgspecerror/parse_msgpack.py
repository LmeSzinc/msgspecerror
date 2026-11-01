"""
MessagePack binary walker for finding and fixing invalid UTF-8 in string fields.

Provides two strategies:
- ``fixup_msgpack_unicode_fast`` -- target a single bad string using
  ``UnicodeDecodeError`` metadata (O(N) search, one fix).
- ``fixup_msgpack_unicode_slow`` -- walk the entire msgpack structure and
  fix every string with invalid UTF-8 (O(N) one-pass).
"""
import struct
from collections import deque

from .const import T_utf8_error

# ── pre-compiled structs ─────────────────────────────────────────────────

_U16BE = struct.Struct('>H')
_U32BE = struct.Struct('>I')

# Marks a repaired span whose original bytes can not be told apart from another
# one: two different items repaired to the same bytes, so restoring either of
# them would be a guess.
AMBIGUOUS_SPAN = object()
MISSING = object()


def _record_span(spans, original, repaired):
    """
    Remember which string a rewrite replaced.

    Every string has to be valid utf-8 before the payload can be decoded without
    a target type, but a `msgspec.Raw` field is only rewritten because of that
    step: the typed decoder never reads it. The repaired text is the key, so a
    raw field filled from the repaired payload can be traced back to the bytes
    the payload really had.

    The key is the decoded text, not the bytes: the value is looked up in a
    decoded object (`_restore_rewrites()`), where a `str` and a `bytes` are
    already told apart, so a binary blob can never be mistaken for a string.

    Args:
        spans (dict | None): Map of repaired text to the original item bytes,
            updated in place. None when the caller does not need it.
        original (bytes): The whole item (header and payload) before the
            rewrite, it is written back as a `msgspec.Raw` verbatim
        repaired (str): The text it became
    """
    if spans is None:
        return
    previous = spans.get(repaired, MISSING)
    if previous is MISSING:
        spans[repaired] = original
    elif previous != original:
        # Two strings repair to the same text and came from different bytes, so
        # neither original can be restored without guessing
        spans[repaired] = AMBIGUOUS_SPAN


# ── helpers ──────────────────────────────────────────────────────────────

def _make_str_header(n):
    """
    Build the msgpack str header bytes for a payload of *n* bytes.

    Args:
        n (int): Payload length in bytes.

    Returns:
        bytes: Header bytes (fixstr, str8, str16, or str32 as appropriate).
    """
    if n <= 31:
        return bytes([0xa0 + n])
    if n < 256:
        return bytes([0xd9, n])
    if n < 65536:
        return b'\xda' + _U16BE.pack(n)
    return b'\xdb' + _U32BE.pack(n)


# ── header detection (fast method) ───────────────────────────────────────

def _check_str_header_at(data, payload_start, str_len):
    """
    Check whether the bytes immediately before *payload_start* form a valid
    msgpack string header for a string of length *str_len*.

    Args:
        data (bytes): The raw msgpack buffer.
        payload_start (int): Byte offset where the string payload begins.
        str_len (int): Expected payload length.

    Returns:
        int | None: Header byte length (1, 2, 3, or 5) when valid,
        ``None`` otherwise.
    """
    # fixstr (0xa0-0xbf)
    if payload_start >= 1:
        h = data[payload_start - 1]
        if 0xa0 <= h <= 0xbf and (h - 0xa0) == str_len:
            return 1

    # str8  (0xd9 + 1-byte length)
    if payload_start >= 2 and str_len < 256:
        h1 = data[payload_start - 2]
        h2 = data[payload_start - 1]
        if h1 == 0xd9 and h2 == str_len:
            return 2

    # str16 (0xda + 2-byte big-endian length)
    if payload_start >= 3 and str_len < 65536:
        if data[payload_start - 3] == 0xda:
            actual_len = _U16BE.unpack_from(data, payload_start - 2)[0]
            if actual_len == str_len:
                return 3

    # str32 (0xdb + 4-byte big-endian length)
    if payload_start >= 5:
        if data[payload_start - 5] == 0xdb:
            actual_len = _U32BE.unpack_from(data, payload_start - 4)[0]
            if actual_len == str_len:
                return 5

    return None


# ── slow-walker helpers ─────────────────────────────────────────────────

def _try_fix_string(data, header_len, str_len, item_start, utf8_error):
    """
    Decode the string at *item_start* and report it when it needs a rewrite.

    The buffer is never modified: the rewrite is reported as a span, so the
    caller can copy the whole payload in one ``b''.join()`` at the end.

    Args:
        data (bytes): The msgpack buffer.
        header_len (int): Byte length of the str header.
        str_len (int): Byte length of the payload.
        item_start (int): Offset of the entire item (header + payload).
        utf8_error (str): Error handler passed to ``bytes.decode()`` /
            ``.encode()`` (e.g. ``'replace'``, ``'ignore'``).

    Returns:
        tuple[int, tuple | None]: The byte position immediately after this item,
            and ``(item_start, item_end, fixed)`` when it was rewritten
    """
    payload_start = item_start + header_len
    item_end = payload_start + str_len
    # `data` is normalized to `bytes` by the caller, so this slice is one too
    payload = data[payload_start:item_end]
    try:
        payload.decode('utf-8')
    except UnicodeDecodeError:
        fixed = payload.decode('utf-8', utf8_error).encode('utf-8', utf8_error)
        return item_end, (item_start, item_end, fixed)
    return item_end, None


# ── iterative walker ────────────────────────────────────────────────────

def _walk_fix(data, start, utf8_error):
    """
    Walk the msgpack structure from *start*, reporting every string with
    invalid UTF-8 encountered along the way.

    Uses an explicit :class:`collections.deque` as a stack instead of
    recursion to avoid hitting Python's recursion limit on deeply nested
    inputs.

    The buffer is read, never written: a rewritten string would change the
    length, so the walk yields its span and the caller copies the payload once.

    Args:
        data (bytes): The msgpack buffer, which is not modified.
        start (int): Offset to begin processing.
        utf8_error (str): Error handler for bytes decode/encode.

    Yields:
        tuple[int, int, bytes]: The start and end offset of an item that has to
            be rewritten, and the text that replaces its payload
    """
    pos = start
    # Stack of remaining-item counts for enclosing containers.
    # For a map with N entries we push N*2 (keys + values).
    # For an array with N elements we push N.
    stack = deque()
    # Cached count of remaining items in the current (top) container.
    # 0 means no container context (stack is empty or all finished).
    remain = 0
    total = len(data)

    while True:
        if pos >= total:
            return

        op = data[pos]

        # ── container headers (with children) — push + continue onsite ──
        if 0x80 <= op <= 0x8f:  # fixmap
            op -= 0x80
            if op:
                op *= 2
                stack.append(op)
                remain = op
            pos += 1
            continue
        if 0x90 <= op <= 0x9f:  # fixarray
            op -= 0x90
            if op:
                stack.append(op)
                remain = op
            pos += 1
            continue

        # ── positive fixint 0x00 .. 0x7f ──
        if op <= 0x7f:
            pos += 1
        # ── fixstr 0xa0 .. 0xbf ──
        elif 0xa0 <= op <= 0xbf:
            pos, span = _try_fix_string(data, 1, op - 0xa0, pos, utf8_error)
            if span is not None:
                yield span
        # ── nil 0xc0 ──
        elif op == 0xc0:
            pos += 1
        # ── (never used) 0xc1 ──
        elif op == 0xc1:
            pos += 1
        # ── false 0xc2 / true 0xc3 ──
        elif op in (0xc2, 0xc3):
            pos += 1
        # ── bin8 0xc4 ──
        elif op == 0xc4:
            pos += 2 + data[pos + 1]
        # ── bin16 0xc5 ──
        elif op == 0xc5:
            blen = _U16BE.unpack_from(data, pos + 1)[0]
            pos += 3 + blen
        # ── bin32 0xc6 ──
        elif op == 0xc6:
            blen = _U32BE.unpack_from(data, pos + 1)[0]
            pos += 5 + blen
        # ── ext8 0xc7 ──
        elif op == 0xc7:
            elen = data[pos + 1]
            pos += 3 + elen
        # ── ext16 0xc8 ──
        elif op == 0xc8:
            elen = _U16BE.unpack_from(data, pos + 1)[0]
            pos += 4 + elen
        # ── ext32 0xc9 ──
        elif op == 0xc9:
            elen = _U32BE.unpack_from(data, pos + 1)[0]
            pos += 6 + elen
        # ── float32 0xca ──
        elif op == 0xca:
            pos += 5
        # ── float64 0xcb ──
        elif op == 0xcb:
            pos += 9
        # ── uint8 0xcc ──
        elif op == 0xcc:
            pos += 2
        # ── uint16 0xcd ──
        elif op == 0xcd:
            pos += 3
        # ── uint32 0xce ──
        elif op == 0xce:
            pos += 5
        # ── uint64 0xcf ──
        elif op == 0xcf:
            pos += 9
        # ── int8 0xd0 ──
        elif op == 0xd0:
            pos += 2
        # ── int16 0xd1 ──
        elif op == 0xd1:
            pos += 3
        # ── int32 0xd2 ──
        elif op == 0xd2:
            pos += 5
        # ── int64 0xd3 ──
        elif op == 0xd3:
            pos += 9
        # ── fixext1 0xd4 ──
        elif op == 0xd4:
            pos += 3
        # ── fixext2 0xd5 ──
        elif op == 0xd5:
            pos += 4
        # ── fixext4 0xd6 ──
        elif op == 0xd6:
            pos += 6
        # ── fixext8 0xd7 ──
        elif op == 0xd7:
            pos += 10
        # ── fixext16 0xd8 ──
        elif op == 0xd8:
            pos += 18
        # ── str8 0xd9 ──
        elif op == 0xd9:
            pos, span = _try_fix_string(data, 2, data[pos + 1], pos, utf8_error)
            if span is not None:
                yield span
        # ── str16 0xda ──
        elif op == 0xda:
            slen = _U16BE.unpack_from(data, pos + 1)[0]
            pos, span = _try_fix_string(data, 3, slen, pos, utf8_error)
            if span is not None:
                yield span
        # ── str32 0xdb ──
        elif op == 0xdb:
            slen = _U32BE.unpack_from(data, pos + 1)[0]
            pos, span = _try_fix_string(data, 5, slen, pos, utf8_error)
            if span is not None:
                yield span

        # ── array16 / array32 / map16 / map32 ──
        elif op == 0xdc:
            n = _U16BE.unpack_from(data, pos + 1)[0]
            if n:
                stack.append(n)
                remain = n
            pos += 3
            continue
        elif op == 0xdd:
            n = _U32BE.unpack_from(data, pos + 1)[0]
            if n:
                stack.append(n)
                remain = n
            pos += 5
            continue
        elif op == 0xde:
            n = _U16BE.unpack_from(data, pos + 1)[0]
            if n:
                n *= 2
                stack.append(n)
                remain = n
            pos += 3
            continue
        elif op == 0xdf:
            n = _U32BE.unpack_from(data, pos + 1)[0]
            if n:
                n *= 2
                stack.append(n)
                remain = n
            pos += 5
            continue

        # ── negative fixint 0xe0 .. 0xff ──
        elif op >= 0xe0:
            pos += 1

        else:
            # Unknown opcode – skip one byte so the walker doesn't hang
            pos += 1

        # ── container bookkeeping ──
        # Decrement the cached remain counter.  When it reaches 0,
        # pop the frame and cascade to parent containers.
        if remain:
            remain -= 1
            if remain > 0:
                continue

        # Current container (or a parent) has been fully consumed.
        while stack:
            stack.pop()
            if not stack:
                return
            # Decrement the parent container
            remain = stack[-1] - 1
            if remain > 0:
                stack[-1] = remain
                break
            # Parent also empty, loop to pop it

        if not stack:
            return


# ── fast method ─────────────────────────────────────────────────────────

def fixup_msgpack_unicode_fast(
        data,
        error: UnicodeDecodeError,
        utf8_error: T_utf8_error = 'replace',
        spans=None,
):
    """
    Locate the exact msgpack string that caused *error* and fix it.

    Searches the raw msgpack buffer for the failing string's payload
    (``error.object``) and validates that the preceding bytes form a valid
    msgpack string header.  If exactly **one** candidate is found the
    payload is repaired in place and the fixed buffer is returned.

    When there are multiple candidates (e.g. the same payload appears inside
    a binary blob, or there are duplicate strings) the function returns
    ``None`` and the caller should fall back to the slow walker.

    Args:
        data (bytes): The original msgpack buffer.
        error (UnicodeDecodeError): The error raised during decoding.
        utf8_error (str): Error handling scheme.  ``'replace'`` substitutes
            U+FFFD, ``'ignore'`` drops invalid bytes, ``'strict'``
            re-raises (not useful here).

    Returns:
        bytes | None: The fixed ``bytes`` on success, ``None`` when the
            failing string cannot be unambiguously located.
    """
    if type(data) is not bytes:
        # A `memoryview` has no `.find()`; the repair only runs on a failed
        # decode, so one copy here is cheap and every step below is uniform
        data = bytes(data)
    payload = error.object
    str_len = len(payload)

    # Collect all (position, header_len) candidates
    candidates = []
    pos = 0
    while True:
        pos = data.find(payload, pos)
        if pos == -1:
            break
        hdr_len = _check_str_header_at(data, pos, str_len)
        if hdr_len is not None:
            candidates.append((pos, hdr_len))
        pos += 1

    if len(candidates) != 1:
        # Ambiguous – let the caller fall back to the slow walker.
        return None

    payload_start, header_len = candidates[0]

    # Decode with the requested error handler and re-encode
    fixed_payload = payload.decode('utf-8', utf8_error).encode('utf-8', utf8_error)
    new_header = _make_str_header(len(fixed_payload))

    old_total = header_len + str_len

    # ba = bytearray(data)
    # ba[payload_start - header_len:payload_start - header_len + old_total] = new_header + fixed_payload
    # return bytes(ba)

    # joining bytes is faster than converting to bytearray then convert back to bytes
    start = payload_start - header_len
    end = start + old_total
    _record_span(spans, data[start:end], fixed_payload.decode('utf-8'))
    return b''.join((data[:start], new_header, fixed_payload, data[end:]))


# ── slow method ─────────────────────────────────────────────────────────

def fixup_msgpack_unicode_slow(
        data,
        utf8_error: T_utf8_error = 'replace',
        spans=None,
):
    """
    Walk the entire msgpack structure and fix every string with invalid UTF-8.

    This is a **one-pass O(N)** walk — the entire buffer is traversed once
    regardless of how many strings are repaired.  Binary segments (``bin``,
    ``ext``) are skipped, so opaque payloads are never touched.

    The repaired payload is copied once at the end: the walk only reports the
    spans to replace, so a buffer with many bad strings costs one join instead
    of one relocation per string.

    Args:
        data (bytes): The msgpack buffer to repair.
        utf8_error (str): Error handling scheme passed to
            ``bytes.decode()`` / ``.encode()`` when a string is not valid
            UTF-8.  ``'replace'`` (default) substitutes U+FFFD,
            ``'ignore'`` drops invalid bytes.
            ``'strict'`` would re-raise and is not useful here.
        spans (dict | None): Map of repaired item bytes to the original ones,
            filled in for every rewrite. Defaults to None.

    Returns:
        bytes: The fully repaired msgpack bytes.
    """
    if type(data) is not bytes:
        # A `memoryview` slice has no `.decode()` and a `bytearray` slice would
        # end up in the span map as a mutable object; the repair only runs on a
        # failed decode, so one copy here keeps every step uniform
        data = bytes(data)
    parts = []
    end = 0
    for start, item_end, fixed in _walk_fix(data, 0, utf8_error):
        parts.append(data[end:start])
        parts.append(_make_str_header(len(fixed)))
        parts.append(fixed)
        end = item_end
        _record_span(spans, data[start:item_end], fixed.decode('utf-8'))
    if not parts:
        return data
    parts.append(data[end:])
    return b''.join(parts)
