"""Every loader accepts `bytes`, `bytearray` and `memoryview`, and says the same thing.

`T_json_data` is `bytes | bytearray | memoryview | str` and `T_msgpack_data` is
`bytes | bytearray | memoryview`, so a caller may hand over any of them. The type
was advertised but never exercised, and one path was broken because of it: the
utf-8 repair decodes the payload itself, and a `memoryview` has no `.decode()`.
`load_json_with_default(..., utf8_error='replace')` raised `AttributeError`, and
the msgpack one raised it from `.find()` — a payload type turned a data error
into a crash of the library.

The contract under test is strongest stated this way: the payload type must not
change the answer. Every case is run once per accepted type and the outcomes are
compared, so a path that only works for `bytes` fails here even when it does not
raise.
"""
from typing import Dict, List

import msgspec
import pytest
from msgspec import NODEFAULT, Struct

from msgspecerror import (
    convert_with_default, convert_with_error_detail, load_json_with_default, load_json_with_error,
    load_json_with_error_detail, load_msgpack_with_default, load_msgpack_with_error,
    load_msgpack_with_error_detail
)

JSON_TYPES = [bytes, bytearray, memoryview, str]
MSGPACK_TYPES = [bytes, bytearray, memoryview]


class Req(Struct):
    """`a` is required, so a repair has no default to fall back to."""
    a: int
    b: str = 'x'


class Rep(Struct):
    """Every field has a default, so a repair can always answer."""
    a: int = 0
    b: str = 'x'


class RawReq(Struct):
    payload: msgspec.Raw
    a: str = ''


class Text(Struct):
    """A str field, so a repaired payload passes once the bytes are gone."""
    a: str = ''


def as_json(raw, kind):
    """The same json payload as the requested type."""
    if kind is bytes:
        return raw
    if kind is bytearray:
        return bytearray(raw)
    if kind is memoryview:
        return memoryview(raw)
    if kind is str:
        return raw.decode()
    raise TypeError(kind)


def as_msgpack(raw, kind):
    """The same msgpack payload as the requested type."""
    if kind is bytes:
        return raw
    if kind is bytearray:
        return bytearray(raw)
    if kind is memoryview:
        return memoryview(raw)
    raise TypeError(kind)


def describe(error):
    """The fields of an ErrorInfo / ErrorInfoDetail, whatever its defaults are."""
    names = [f.name for f in msgspec.structs.fields(type(error))]
    return {name: getattr(error, name) for name in names}


def outcome(fn, payload, model, **kwargs):
    """Run a single-error loader and describe the answer in a type-independent way."""
    try:
        result, error = fn(payload, model, **kwargs)
    except Exception as e:
        return ('raised', type(e).__name__, str(e))
    if result is NODEFAULT:
        return ('error', describe(error))
    return ('ok', result)


def outcome_default(fn, payload, model, **kwargs):
    """Run a repairing loader, which answers with a result and a list of errors."""
    try:
        result, errors = fn(payload, model, **kwargs)
    except Exception as e:
        return ('raised', type(e).__name__, str(e))
    return ('ok' if result is not NODEFAULT else 'error', result, [describe(e) for e in errors])


def json_kinds(can_be_a_str):
    """The payload types that can carry this json payload."""
    return JSON_TYPES if can_be_a_str else JSON_TYPES[:-1]


def assert_same(answers):
    """Every payload type has to produce one and the same answer."""
    assert answers.count(answers[0]) == len(answers), answers


# --- Payloads ---

JSON_VALID = b'{"a": 1}'
JSON_TYPE_ERROR = b'{"a": "bad"}'
JSON_BAD_UTF8 = b'{"a": "caf\xe9"}'
JSON_MALFORMED = b'{"a": '
JSON_DEEP = b'{"a": {"b": {"c": [1, "x"]}}}'

MSGPACK_VALID = msgspec.msgpack.encode({'a': 1})
MSGPACK_TYPE_ERROR = msgspec.msgpack.encode({'a': 'bad'})
# `{"a": "caf\xe9"}`: the str header says 4 bytes, the last one is not utf-8
MSGPACK_BAD_UTF8 = b'\x81\xa1a\xa4caf\xe9'
MSGPACK_MALFORMED = b'\x82\xa1a\xa3bad'

# `(id, payload, can_be_a_str)`. Invalid utf-8 can not be written as a `str`,
# so those cases only run the three byte types: comparing them against a
# `str` payload would compare two different inputs.
JSON_CASES = [
    ('valid', JSON_VALID, True),
    ('type-error', JSON_TYPE_ERROR, True),
    ('bad-utf8', JSON_BAD_UTF8, False),
    ('malformed', JSON_MALFORMED, True),
]
JSON_CASE_IDS = [name for name, _, _ in JSON_CASES]

MSGPACK_CASES = [
    ('valid', MSGPACK_VALID),
    ('type-error', MSGPACK_TYPE_ERROR),
    ('bad-utf8', MSGPACK_BAD_UTF8),
    ('malformed', MSGPACK_MALFORMED),
]
MSGPACK_CASE_IDS = [name for name, _ in MSGPACK_CASES]


class TestJsonPayloadTypes:
    """Every json loader answers the same for bytes / bytearray / memoryview / str."""

    @pytest.mark.parametrize('name,raw,as_str', JSON_CASES, ids=JSON_CASE_IDS)
    def test_load_json_with_error(self, name, raw, as_str):
        assert_same([outcome(load_json_with_error, as_json(raw, kind), Req) for kind in json_kinds(as_str)])

    @pytest.mark.parametrize('name,raw,as_str', JSON_CASES, ids=JSON_CASE_IDS)
    def test_load_json_with_error_detail(self, name, raw, as_str):
        assert_same([
            outcome(load_json_with_error_detail, as_json(raw, kind), Req) for kind in json_kinds(as_str)
        ])

    @pytest.mark.parametrize('name,raw,as_str', JSON_CASES, ids=JSON_CASE_IDS)
    @pytest.mark.parametrize('guess', [False, True], ids=['no-guess', 'guess'])
    def test_load_json_with_default(self, name, raw, as_str, guess):
        assert_same([
            outcome_default(load_json_with_default, as_json(raw, kind), Rep, guess_default=guess)
            for kind in json_kinds(as_str)
        ])

    @pytest.mark.parametrize('name,raw,as_str', JSON_CASES, ids=JSON_CASE_IDS)
    @pytest.mark.parametrize('utf8_error', ['replace', 'ignore'])
    def test_load_json_with_default_utf8_error(self, name, raw, as_str, utf8_error):
        assert_same([
            outcome_default(
                load_json_with_default, as_json(raw, kind), Rep,
                guess_default=True, utf8_error=utf8_error)
            for kind in json_kinds(as_str)
        ])

    def test_the_utf8_repair_actually_runs(self):
        """The control: without this the comparison above could pass on a crash."""
        answers = [
            outcome_default(load_json_with_default, as_json(JSON_BAD_UTF8, kind), Text, utf8_error='replace')
            for kind in json_kinds(False)
        ]

        assert answers[0][0] == 'ok', answers[0]
        assert answers[0][1] == Text(a='caf\ufffd')
        assert [e['type'].name for e in answers[0][2]] == ['UNICODE_DECODE_ERROR']
        assert_same(answers)

    @pytest.mark.parametrize('kind', JSON_TYPES, ids=[k.__name__ for k in JSON_TYPES])
    def test_a_deep_error_keeps_its_path(self, kind):
        model = Dict[str, Dict[str, Dict[str, List[int]]]]

        _, detail = load_json_with_error_detail(as_json(JSON_DEEP, kind), model)

        assert detail.loc == ('a', 'b', 'c', 1)
        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('kind', JSON_TYPES, ids=[k.__name__ for k in JSON_TYPES])
    def test_a_raw_field_is_filled(self, kind):
        """The untyped decode that fills the raw field reads the payload itself."""
        raw = b'{"payload": {"a": 1}, "a": "x"}'

        result, errors = load_json_with_default(as_json(raw, kind), RawReq)

        assert errors == []
        assert msgspec.json.decode(result.payload) == {'a': 1}


class TestMsgpackPayloadTypes:
    """Every msgpack loader answers the same for bytes / bytearray / memoryview."""

    @pytest.mark.parametrize('name,raw', MSGPACK_CASES, ids=MSGPACK_CASE_IDS)
    def test_load_msgpack_with_error(self, name, raw):
        assert_same([outcome(load_msgpack_with_error, as_msgpack(raw, kind), Req) for kind in MSGPACK_TYPES])

    @pytest.mark.parametrize('name,raw', MSGPACK_CASES, ids=MSGPACK_CASE_IDS)
    def test_load_msgpack_with_error_detail(self, name, raw):
        assert_same([
            outcome(load_msgpack_with_error_detail, as_msgpack(raw, kind), Req) for kind in MSGPACK_TYPES
        ])

    @pytest.mark.parametrize('name,raw', MSGPACK_CASES, ids=MSGPACK_CASE_IDS)
    @pytest.mark.parametrize('guess', [False, True], ids=['no-guess', 'guess'])
    def test_load_msgpack_with_default(self, name, raw, guess):
        assert_same([
            outcome_default(load_msgpack_with_default, as_msgpack(raw, kind), Rep, guess_default=guess)
            for kind in MSGPACK_TYPES
        ])

    @pytest.mark.parametrize('name,raw', MSGPACK_CASES, ids=MSGPACK_CASE_IDS)
    @pytest.mark.parametrize('utf8_error', ['replace', 'ignore'])
    def test_load_msgpack_with_default_utf8_error(self, name, raw, utf8_error):
        assert_same([
            outcome_default(
                load_msgpack_with_default, as_msgpack(raw, kind), Rep,
                guess_default=True, utf8_error=utf8_error)
            for kind in MSGPACK_TYPES
        ])

    def test_the_utf8_repair_actually_runs(self):
        answers = [
            outcome_default(
                load_msgpack_with_default, as_msgpack(MSGPACK_BAD_UTF8, kind), Text, utf8_error='replace')
            for kind in MSGPACK_TYPES
        ]

        assert answers[0][0] == 'ok', answers[0]
        assert answers[0][1] == Text(a='caf\ufffd')
        assert [e['type'].name for e in answers[0][2]] == ['UNICODE_DECODE_ERROR']
        assert_same(answers)

    @pytest.mark.parametrize('kind', MSGPACK_TYPES, ids=[k.__name__ for k in MSGPACK_TYPES])
    def test_a_deep_error_keeps_its_path(self, kind):
        pack = msgspec.msgpack.encode({'a': {'b': {'c': [1, 'x']}}})
        model = Dict[str, Dict[str, Dict[str, List[int]]]]

        _, detail = load_msgpack_with_error_detail(as_msgpack(pack, kind), model)

        assert detail.loc == ('a', 'b', 'c', 1)
        assert detail.error_value == 'x'

    @pytest.mark.parametrize('kind', MSGPACK_TYPES, ids=[k.__name__ for k in MSGPACK_TYPES])
    def test_a_raw_field_is_filled(self, kind):
        pack = msgspec.msgpack.encode({'payload': {'a': 1}, 'a': 'x'})

        result, errors = load_msgpack_with_default(as_msgpack(pack, kind), RawReq)

        assert errors == []
        assert msgspec.msgpack.decode(result.payload) == {'a': 1}


class TestAnUnsupportedPayloadType:
    """A payload type msgspec does not take is a caller bug, and it is raised."""

    def test_a_str_where_msgpack_is_expected(self):
        with pytest.raises(TypeError):
            load_msgpack_with_default('{"a": 1}', Rep)

    def test_a_list_is_not_a_payload(self):
        with pytest.raises(TypeError):
            load_json_with_default(['a'], Rep)


class TestConvertTakesAnObject:
    """`convert_*` takes a decoded object, so a payload type is just a value."""

    @pytest.mark.parametrize('data', [bytearray(b'ab'), memoryview(b'ab')])
    def test_a_payload_shaped_object_is_not_decoded(self, data):
        result, detail = convert_with_error_detail(data, Rep)

        assert result is NODEFAULT
        assert detail.loc == ()
        assert detail.loc_resolved is True

    def test_a_default_is_still_reachable(self):
        result, errors = convert_with_default({'a': 'bad'}, Rep, guess_default=True)

        assert result == Rep(a=0)
        assert [e.loc for e in errors] == [('a',)]
