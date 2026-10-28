"""Tests for the `*_with_error` family: first error, no repair, no resolution."""
from typing import Dict, List

import msgspec
import pytest
from msgspec import NODEFAULT, Struct
from msgspec.json import decode as decode_json

import msgspecerror.with_error as with_error_mod
from msgspecerror import (
    DECODE_ERRORS,
    ErrorInfo,
    convert_with_error,
    load_json_with_error,
    load_msgpack_with_error,
    parse_msgspec_error,
)
from msgspecerror.const import ErrorType


# --- Test Models ---

class Req(Struct, forbid_unknown_fields=True):
    a: int
    b: str = ''


class Box(Struct):
    inner: Req
    tag: str = 't'


def assert_failed(result):
    obj, error = result
    assert obj is NODEFAULT
    assert isinstance(error, ErrorInfo)
    return error


class TestSuccess:
    def test_convert(self):
        obj, error = convert_with_error({'a': 1}, Req)
        assert obj == Req(a=1)
        assert error is NODEFAULT

    def test_load_json(self):
        obj, error = load_json_with_error(b'{"a": 1}', Req)
        assert obj == Req(a=1)
        assert error is NODEFAULT

    def test_load_msgpack(self):
        obj, error = load_msgpack_with_error(msgspec.msgpack.encode({'a': 1}), Req)
        assert obj == Req(a=1)
        assert error is NODEFAULT

    def test_decoder_is_accepted(self):
        obj, error = load_json_with_error(b'{"a": 1}', msgspec.json.Decoder(Req))
        assert obj == Req(a=1)
        assert error is NODEFAULT

    def test_msgpack_decoder_is_accepted(self):
        obj, error = load_msgpack_with_error(
            msgspec.msgpack.encode({'a': 1}), msgspec.msgpack.Decoder(Req))
        assert obj == Req(a=1)
        assert error is NODEFAULT


class TestValidationError:
    def test_convert(self):
        error = assert_failed(convert_with_error({'a': 'bad'}, Req))
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('a',)

    def test_load_json(self):
        error = assert_failed(load_json_with_error(b'{"a": "bad"}', Req))
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('a',)

    def test_load_msgpack(self):
        error = assert_failed(
            load_msgpack_with_error(msgspec.msgpack.encode({'a': 'bad'}), Req))
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('a',)

    def test_missing_field(self):
        error = assert_failed(convert_with_error({}, Req))
        assert error.type is ErrorType.MISSING_FIELD
        assert error.loc == ('a',)


class TestPlaceholderIsKept:
    """`_with_error` never resolves `...`, that is `_with_error_detail`'s job."""

    def test_dict_placeholder(self):
        error = assert_failed(convert_with_error({'k': 'x'}, Dict[str, int]))
        assert error.loc == ('...',)

    def test_dict_key_placeholder(self):
        error = assert_failed(convert_with_error({1: 'x'}, Dict[str, int]))
        assert error.loc == ('...key',)

    def test_nested_placeholder(self):
        error = assert_failed(
            convert_with_error({'k': [1, 'x']}, Dict[str, List[int]]))
        assert error.loc == ('...', 1)

    def test_matches_parse_msgspec_error(self):
        try:
            msgspec.convert({'k': 'x'}, type=Dict[str, int])
        except msgspec.ValidationError as e:
            expected = parse_msgspec_error(e)
        error = assert_failed(convert_with_error({'k': 'x'}, Dict[str, int]))
        assert error == expected


class TestBadPayload:
    def test_json_malformed(self):
        error = assert_failed(load_json_with_error(b'not json', Req))
        assert error.loc == ()
        assert error.type is ErrorType.JSON_MALFORMED

    def test_json_truncated(self):
        error = assert_failed(load_json_with_error(b'{"a": 1, "b": "x"', Req))
        assert error.loc == ()
        assert error.type is ErrorType.DATA_TRUNCATED

    def test_json_invalid_utf8(self):
        error = assert_failed(load_json_with_error(b'{"a": 1, "b": "\xff"}', Req))
        assert error.loc == ()
        assert error.type is ErrorType.UNICODE_DECODE_ERROR

    def test_json_str_lone_surrogate(self):
        """
        A str payload is encoded to utf-8 before parsing, a lone surrogate is
        the same bad-input class as an invalid byte sequence.
        """
        payload = '{"a": "' + '\ud800' + '"}'
        error = assert_failed(load_json_with_error(payload, Req))
        assert error.loc == ()
        assert error.type is ErrorType.UNICODE_DECODE_ERROR

    def test_msgpack_malformed(self):
        error = assert_failed(load_msgpack_with_error(b'\xc1', Req))
        assert error.loc == ()
        assert error.type is ErrorType.MSGPACK_MALFORMED

    def test_msgpack_truncated(self):
        error = assert_failed(load_msgpack_with_error(b'\x82\xa1a\x01\xa1b', Req))
        assert error.loc == ()
        assert error.type is ErrorType.DATA_TRUNCATED

    def test_msgpack_invalid_utf8(self):
        error = assert_failed(
            load_msgpack_with_error(b'\x82\xa1a\x01\xa1b\xa1\xff', Req))
        assert error.loc == ()
        assert error.type is ErrorType.UNICODE_DECODE_ERROR


class TestDecodeErrors:
    """`DECODE_ERRORS` must cover every bad-input error of a decode call."""

    def test_covers_both_codec_directions(self):
        assert UnicodeDecodeError in DECODE_ERRORS
        assert UnicodeEncodeError in DECODE_ERRORS

    @pytest.mark.parametrize('payload', [
        # invalid utf-8 byte, decoded to str
        b'{"a": "\xff"}',
        # lone surrogate in a str payload, encoded to utf-8 before parsing
        '{"a": "\ud800"}',
        # truncated payload
        b'{"a": ',
        # plain validation failure
        b'{"a": "x"}',
    ], ids=['invalid-utf8', 'lone-surrogate', 'truncated', 'validation'])
    def test_except_catches_every_bad_payload(self, payload):
        """A single `except DECODE_ERRORS` is enough around a decode call."""
        with pytest.raises(DECODE_ERRORS):
            decode_json(payload, type=Req)


class TestCallerBugRaises:
    """A wrong argument is a bug, not bad data, so it is not converted."""

    def test_msgpack_str_payload(self):
        with pytest.raises(TypeError):
            load_msgpack_with_error('abc', Req)

    def test_msgpack_str_payload_detail(self):
        from msgspecerror import load_msgpack_with_error_detail
        with pytest.raises(TypeError):
            load_msgpack_with_error_detail('abc', Req)

    @pytest.mark.parametrize('bad_model', [
        12345,        # not a type at all
        object(),     # an instance, not a type
        'int',        # a str, not a type
    ])
    def test_illegal_type_hint_convert(self, bad_model):
        with pytest.raises(TypeError):
            convert_with_error({}, bad_model)

    def test_illegal_type_hint_json(self):
        with pytest.raises(TypeError):
            load_json_with_error(b'{}', 12345)

    def test_illegal_type_hint_msgpack(self):
        with pytest.raises(TypeError):
            load_msgpack_with_error(b'\x80', 12345)


class TestSingleDecode:
    def test_json_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_json

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_json', counting)
        load_json_with_error(b'{"a": "bad"}', Req)
        assert len(calls) == 1

    def test_msgpack_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_msgpack

        def counting(*args, **kwargs):
            calls.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_msgpack', counting)
        load_msgpack_with_error(msgspec.msgpack.encode({'a': 'bad'}), Req)
        assert len(calls) == 1


class TestNoMutation:
    def test_convert_does_not_modify_data(self):
        data = {'a': 'bad'}
        convert_with_error(data, Req)
        assert data == {'a': 'bad'}

    def test_dict_value_is_not_deleted(self):
        """The auto-repair loader deletes the failing entry, this family must not."""
        data = {'items': {'apple': {'amount': 'ABC'}}}
        model = Dict[str, Dict[str, int]]
        assert_failed(convert_with_error(data, model))
        assert data == {'items': {'apple': {'amount': 'ABC'}}}
