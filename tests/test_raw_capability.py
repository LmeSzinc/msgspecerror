"""
Coverage for the `msgspec.Raw` capability table in `doc/8-resolve-error.md` §3.9.1.

Every row of that table is pinned here, so a change in msgspec or in the repair
engine can not quietly move a cell. Rows already covered elsewhere are covered
again only when the table states a different answer for another family.

The table is a contract, not a wish: a case that can not be satisfied is marked
`xfail` with the reason, instead of being dropped.
"""

import copy
import dataclasses
from typing import Dict, List, NamedTuple, Optional, Union

import msgspec
import pytest
from msgspec import NODEFAULT, UNSET, Raw, Struct
from typing_extensions import TypedDict

import msgspecerror.const
from msgspecerror import (
    convert_with_default, convert_with_error, convert_with_error_detail, load_json_with_default, load_json_with_error,
    load_json_with_error_detail, load_msgpack_with_default, load_msgpack_with_error, load_msgpack_with_error_detail
)
from msgspecerror.const import MSGSPEC_0_19_PLUS, ErrorType
from msgspecerror.parse_error import parse_msgspec_error
from msgspecerror.repair import _restore_rewrites

# msgspec before 0.19.0 refuses a `Raw` instance in `convert`, so a pinned entry
# can not be judged and the failing key is never found. The table documents that
# row as unresolved, so the cases that need a located key are skipped there.
needs_convert_raw = pytest.mark.skipif(
    not MSGSPEC_0_19_PLUS,
    reason='msgspec < 0.19.0 refuses a Raw instance in convert, the key can not be located',
)


class RawReq(Struct):
    """A required raw field beside a field that can fail."""
    payload: Raw
    n: int


class RawDefault(Struct):
    """A raw field with a default, missing from the payload is not an error."""
    payload: Raw = Raw(b'')
    n: int = 0


class ArrRaw(Struct, array_like=True):
    """A raw field inside an array-like struct, the index is the field order."""
    payload: Raw
    n: int


class TDRecord(TypedDict, total=False):
    """A raw field in a TypedDict, `total=False` so it also decodes before 0.22.0."""
    payload: Raw
    n: int


@dataclasses.dataclass
class DCRecord:
    """A raw field in a dataclass."""
    payload: Raw
    n: int


class NTRecord(NamedTuple):
    """A raw field in a NamedTuple, its decoded form is an array."""
    payload: Raw
    n: int


def _restored(value, spans):
    """Restore the rewrites of one pinned leaf."""
    return _restore_rewrites(value, spans)


class TestLoadRawRequired:
    """Row 1: `load_*`, a required raw field, the real error is another field."""

    DOC = b'{"one": {"payload": {"a": 1}, "n": 1}, "two": {"payload": {"a": 2}, "n": "bad"}}'

    def test_with_error_keeps_the_placeholder(self):
        """The untyped decode has no bytes, so the key can not be resolved."""
        obj, error = load_json_with_error(self.DOC, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert error.loc == ('...', 'n')
        assert error.msg.endswith('at `$[...].n`')


@needs_convert_raw
class TestLoadRawDefaulted:
    """Row 2: `load_*`, the raw field has a default."""

    DOC = b'{"one": {"payload": {"a": 1}, "n": 1}, "two": {"n": "bad"}}'

    def test_with_error_keeps_the_placeholder(self):
        obj, error = load_json_with_error(self.DOC, Dict[str, RawDefault])

        assert obj is NODEFAULT
        assert error.loc == ('...', 'n')
        assert error.msg.endswith('at `$[...].n`')

    def test_with_error_detail_resolves(self):
        obj, detail = load_json_with_error_detail(self.DOC, Dict[str, RawDefault])

        assert obj is NODEFAULT
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_with_default_returns_the_model(self):
        result, errors = load_json_with_default(self.DOC, Dict[str, RawDefault], guess_default=True)

        assert sorted(result) == ['one', 'two']
        assert result['one'].n == 1
        assert result['two'].n == 0
        assert isinstance(result['one'].payload, Raw)
        assert [e.loc for e in errors] == [('two', 'n')]


class TestConvertRawRequired:
    """Row 3: `convert_*`, a required raw field, an in-memory object has no bytes."""

    DATA = {'payload': {'a': 1}, 'n': 1}

    def test_with_error_reports_the_raw_field(self):
        """Not a data error: the same payload decodes fine with a decoder."""
        obj, error = convert_with_error(self.DATA, RawReq)

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('payload',)
        assert msgspec.json.decode(msgspec.json.encode(self.DATA), type=RawReq)

    def test_with_error_detail_reports_an_input_rejection(self):
        """Every value is fine, only the raw field complains, so there is no verdict."""
        obj, detail = convert_with_error_detail(self.DATA, RawReq)

        assert obj is NODEFAULT
        assert detail.type is ErrorType.INPUT_REJECTED
        assert detail.loc == ()
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_with_default_fills_the_raw_field(self):
        """No payload, so the value is encoded as msgpack to build the field."""
        data = {'payload': {'a': 1}, 'n': 1}
        result, errors = convert_with_default(data, RawReq)

        assert errors == []
        assert result.n == 1
        assert msgspec.msgpack.decode(result.payload) == {'a': 1}


class TestConvertRawDefaulted:
    """Row 4: `convert_*`, the raw field has a default."""

    DATA = {'payload': {'a': 1}, 'n': 'bad'}

    def test_with_error_reports_the_raw_field(self):
        obj, error = convert_with_error(self.DATA, RawDefault)

        assert obj is NODEFAULT
        assert error.loc == ('payload',)

    def test_with_error_detail_reveals_the_real_error(self):
        """The raw field is pinned, so `convert` reaches the real error at `n`."""
        obj, detail = convert_with_error_detail(self.DATA, RawDefault)

        assert obj is NODEFAULT
        assert detail.type is ErrorType.TYPE_MISMATCH
        assert detail.loc == ('n',)
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_with_default_repairs_the_real_error(self):
        """The raw field is filled, so the error at `n` is reached and repaired."""
        data = {'payload': {'a': 1}, 'n': 'bad'}
        result, errors = convert_with_default(data, RawDefault, guess_default=True)

        assert result.n == 0
        assert msgspec.msgpack.decode(result.payload) == {'a': 1}
        assert [e.loc for e in errors] == [('n',)]
        # The sub-document is never replaced by a placeholder inside the object
        assert data['payload'] == {'a': 1}


class TestRootRaw:
    """Row 5: the model itself is `Raw`."""

    DATA = {'a': 1}
    DOC = b'{"a": 1}'

    def test_with_error_reports_the_document(self):
        obj, error = convert_with_error(self.DATA, Raw)

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ()

    def test_with_error_detail_reports_an_input_rejection(self):
        obj, detail = convert_with_error_detail(self.DATA, Raw)

        assert obj is NODEFAULT
        assert detail.type is ErrorType.INPUT_REJECTED
        assert detail.loc == ()
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_with_default_encodes_the_document(self):
        result, errors = convert_with_default(self.DATA, Raw)

        assert errors == []
        assert msgspec.msgpack.decode(result) == self.DATA

    @pytest.mark.parametrize('loader', [load_json_with_error, load_json_with_error_detail, load_json_with_default])
    def test_load_families_decode_the_document(self, loader):
        """A payload has bytes, so the root `Raw` model is not a problem there."""
        result, errors = loader(self.DOC, Raw)

        assert isinstance(result, Raw)
        assert bytes(result) == self.DOC


class TestOptionalRaw:
    """Row 6: `Optional[Raw]` means `null`, the decoder rejects the object."""

    class Opt(Struct):
        payload: Optional[Raw]
        n: int

    DOC = b'{"one": {"payload": 1, "n": 1}}'

    def test_with_error_reports_the_null_mismatch(self):
        obj, error = load_json_with_error(self.DOC, Dict[str, self.Opt])

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('...', 'payload')
        assert error.ctx.expected == 'null'

    def test_with_error_detail_resolves_the_key(self):
        """`null` is a real check, not the convert artifact of a raw field."""
        obj, detail = load_json_with_error_detail(self.DOC, Dict[str, self.Opt])

        assert obj is NODEFAULT
        assert detail.loc == ('one', 'payload')
        assert detail.loc_resolved is True
        assert detail.error_value == 1
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('guess_default', [False, True])
    def test_with_default_writes_null(self, guess_default):
        """`None` is a member of the union, so no guess is needed to repair it."""
        result, errors = load_json_with_default(self.DOC, Dict[str, self.Opt], guess_default=guess_default)

        assert result == {'one': self.Opt(payload=None, n=1)}
        assert [e.loc for e in errors] == [('one', 'payload')]

    def test_with_default_keeps_the_other_entries(self):
        doc = b'{"good": {"payload": null, "n": 1}, "bad": {"payload": 1, "n": 2}}'

        result, errors = load_json_with_default(doc, Dict[str, self.Opt])

        assert result == {'good': self.Opt(payload=None, n=1), 'bad': self.Opt(payload=None, n=2)}
        assert [e.loc for e in errors] == [('bad', 'payload')]

    def test_with_default_from_msgpack(self):
        doc = msgspec.msgpack.encode({'one': {'payload': 1, 'n': 1}})

        result, errors = load_msgpack_with_default(doc, Dict[str, self.Opt])

        assert result == {'one': self.Opt(payload=None, n=1)}
        assert [e.loc for e in errors] == [('one', 'payload')]

    def test_with_default_from_convert(self):
        data = {'one': {'payload': 1, 'n': 1}}

        result, errors = convert_with_default(data, Dict[str, self.Opt])

        assert result == {'one': self.Opt(payload=None, n=1)}
        assert [e.loc for e in errors] == [('one', 'payload')]


class TestUnionRaw:
    """Row 7: `Union[Raw, int]`, the decoder gives the object to the `int` member."""

    class Uni(Struct):
        v: Union[Raw, int]
        n: int = 0

    DOC = b'{"one": {"v": {"a": 1}}}'

    def test_with_error_reports_the_int_member(self):
        obj, error = load_json_with_error(self.DOC, Dict[str, self.Uni])

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('...', 'v')

    def test_with_error_detail_resolves_the_key(self):
        """The failure belongs to the `int` member, so the key is a real one."""
        obj, detail = load_json_with_error_detail(self.DOC, Dict[str, self.Uni])

        assert obj is NODEFAULT
        assert detail.loc == ('one', 'v')
        assert detail.loc_resolved is True
        assert detail.error_value == {'a': 1}
        assert detail.error_value_resolved is True

    def test_with_default_guesses_the_int_member(self):
        """`0` is the guessed value of the `int` member the object was given to."""
        TWO = b'{"good": {"v": 5, "n": 1}, "bad": {"v": {"a": 1}, "n": 2}}'

        result, errors = load_json_with_default(TWO, Dict[str, self.Uni], guess_default=True)

        assert result == {'good': self.Uni(v=5, n=1), 'bad': self.Uni(v=0, n=2)}
        assert [e.loc for e in errors] == [('bad', 'v')]

    def test_with_default_drops_only_the_bad_entry(self):
        """`v` is required and has no default, so the minimal repair drops its entry."""
        TWO = b'{"good": {"v": 5, "n": 1}, "bad": {"v": {"a": 1}, "n": 2}}'

        result, errors = load_json_with_default(TWO, Dict[str, self.Uni])

        assert result == {'good': self.Uni(v=5, n=1)}
        assert [e.loc for e in errors] == [('bad', 'v')]

    def test_with_default_from_msgpack(self):
        doc = msgspec.msgpack.encode({'bad': {'v': {'a': 1}, 'n': 2}})

        result, errors = load_msgpack_with_default(doc, Dict[str, self.Uni], guess_default=True)

        assert result == {'bad': self.Uni(v=0, n=2)}
        assert [e.loc for e in errors] == [('bad', 'v')]

    def test_with_default_from_convert(self):
        data = {'bad': {'v': {'a': 1}, 'n': 2}}

        result, errors = convert_with_default(data, Dict[str, self.Uni], guess_default=True)

        assert result == {'bad': self.Uni(v=0, n=2)}
        assert [e.loc for e in errors] == [('bad', 'v')]


class TestArrayLikeRaw:
    """Row 8: a raw field inside an `array_like=True` struct.

    The error location is an index, and the walk maps it back through the field
    order of the struct.
    """

    class Sub(Struct, array_like=True):
        payload: Raw
        n: int

    DOC = b'{"one": [{"a": 1}, 1], "two": [{"b": 2}, "bad"]}'

    def test_with_error_detail_resolves_the_failing_key(self):
        obj, detail = load_json_with_error_detail(self.DOC, Dict[str, self.Sub])

        assert obj is NODEFAULT
        assert detail.loc == ('two', 1)
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_the_index_is_still_precise(self):
        obj, detail = load_json_with_error_detail(self.DOC, Dict[str, self.Sub])

        assert obj is NODEFAULT
        assert detail.loc[1] == 1

    def test_with_default_fills_the_failing_position(self):
        """`n` has no default, so the guessed value keeps the shape of the array."""
        result, errors = load_json_with_default(self.DOC, Dict[str, self.Sub], guess_default=True)

        assert list(result) == ['one', 'two']
        assert result['one'].n == 1
        assert result['two'].n == 0
        assert [e.loc for e in errors] == [('two', 1)]

    def test_with_default_without_guessing_drops_only_the_bad_entry(self):
        result, errors = load_json_with_default(self.DOC, Dict[str, self.Sub])

        assert list(result) == ['one']
        assert [e.loc for e in errors] == [('two', 1)]


@needs_convert_raw
class TestMsgpackInvalidUtf8InRaw:
    """Row 9: a msgpack payload whose raw span holds invalid utf-8."""

    PAYLOAD = (
        b'\x82\xa4good\x82\xa7payload\xa3a\xffb\xa1n\x01'
        b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3bad'
    )

    def test_with_error_keeps_the_placeholder(self):
        """The untyped decode rejects the payload, so no key can be found."""
        obj, error = load_msgpack_with_error(self.PAYLOAD, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert error.loc == ('...', 'n')
        assert error.msg.endswith('at `$[...].n`')

    def test_with_error_detail_resolves(self):
        obj, detail = load_msgpack_with_error_detail(self.PAYLOAD, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert detail.loc == ('bad', 'n')
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True

    def test_with_default_returns_the_model(self):
        result, errors = load_msgpack_with_default(self.PAYLOAD, Dict[str, RawReq], guess_default=True)

        assert sorted(result) == ['bad', 'good']
        assert result['bad'].n == 0
        assert isinstance(result['bad'].payload, Raw)
        assert [e.loc for e in errors] == [('bad', 'n')]

    def test_the_located_value_of_a_rewritten_field_is_withdrawn(self):
        """The failing value itself holds the invalid byte, so it is not handed back."""
        payload = (
            b'\x82\xa4good\x82\xa7payload\xa1x\xa1n\x01'
            b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3a\xffb'
        )
        obj, detail = load_msgpack_with_error_detail(payload, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert detail.loc == ('bad', 'n')
        assert detail.loc_resolved is True
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_with_default_leaves_the_valid_payload_alone(self):
        """Without an invalid byte the raw bytes are the original span."""
        payload = (
            b'\x82\xa4good\x82\xa7payload\xa3abc\xa1n\x01'
            b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3bad'
        )
        result, errors = load_msgpack_with_default(payload, Dict[str, RawReq], guess_default=True)

        assert bytes(result['good'].payload) == b'\xa3abc'
        assert bytes(result['bad'].payload) == b'\xa1x'
        assert [e.loc for e in errors] == [('bad', 'n')]


@needs_convert_raw
class TestMsgpackByteRewrite:
    """The unicode repair must not rewrite a raw field that was not in error.

    Every string has to be valid utf-8 before the payload can be decoded without
    a target type. A `msgspec.Raw` field is not read by that decode, so a rewrite
    inside it is collateral. The repair records every item it replaced, and the
    items of a raw field are walked so the original bytes go back, at any depth.
    """

    PAYLOAD = (
        b'\x82\xa4good\x82\xa7payload\xa3a\xffb\xa1n\x01'
        b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3bad'
    )

    def test_the_good_entry_keeps_its_bytes(self):
        result, errors = load_msgpack_with_default(self.PAYLOAD, Dict[str, RawReq], guess_default=True)

        assert bytes(result['good'].payload) == b'\xa3a\xffb'
        assert [e.loc for e in errors] == [('bad', 'n')]

    def test_the_defect_is_confined_to_the_rewritten_span(self):
        """Everything else is still repaired correctly, and the error is located."""
        result, errors = load_msgpack_with_default(self.PAYLOAD, Dict[str, RawReq], guess_default=True)

        assert [e.loc for e in errors] == [('bad', 'n')]
        assert result['bad'].n == 0
        # A value that was never rewritten is handed back as it was
        assert bytes(result['bad'].payload) == b'\xa1x'
        assert bytes(result['good'].payload) == b'\xa3a\xffb'

    def test_a_raw_field_holding_a_sub_document_keeps_its_bytes(self):
        """The usual shape: the raw field holds a whole sub-document."""
        nested = b'\x82\xa7content\xa3a\xffb\xa1n\x01'
        result = self._load_with_good_payload(nested)

        assert bytes(result['good'].payload) == nested

    def test_a_raw_field_holding_deeply_nested_documents(self):
        nested = b'\x81\xa5items\x92\x81\xa2id\x01\x81\xa7content\xa3a\xffb'
        result = self._load_with_good_payload(nested)

        assert bytes(result['good'].payload) == nested

    @pytest.mark.xfail(
        reason='two different invalid strings need more than the two repair attempts',
        strict=True,
    )
    def test_two_different_rewrites_in_one_raw_field(self):
        nested = b'\x82\xa1a\xa3x\xffy\xa1b\xa5p\xf0\x28\x8c\xbc'
        result = self._load_with_good_payload(nested)

        assert bytes(result['good'].payload) == nested

    def test_the_ignore_handler_is_restored_as_well(self):
        nested = b'\x81\xa7content\xa3a\xffb'
        payload = (
            b'\x82\xa4good\x82\xa7payload' + nested + b'\xa1n\x01'
            b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3bad'
        )

        result, errors = load_msgpack_with_default(payload, Dict[str, RawReq], utf8_error='ignore')

        assert bytes(result['good'].payload) == nested
        assert [e.loc for e in errors if e.loc] == [('bad', 'n')]

    def test_two_identical_items_are_not_restored(self):
        """One rewritten and one genuine copy can not be told apart by their bytes."""
        value = ['a\ufffdb', 'a\ufffdb']

        # Which of the two came from the repair is unknown, so neither is touched
        assert _restored(value, {'a\ufffdb': b'a\xffb'}) == value

    def test_a_copy_inside_a_binary_blob_is_not_restored(self):
        """A bin payload is not a string, even when header-like bytes precede it."""
        # A decoded blob is `bytes`, never the repaired text
        value = {'blob': b'\x00a\xef\xbf\xbdb\x00'}

        assert _restored(value, {'a\ufffdb': b'a\xffb'}) == value

    def test_the_string_beside_a_binary_copy_is_restored(self):
        value = {'content': 'a\ufffdb', 'blob': b'\x00a\xef\xbf\xbdb\x00'}

        # The string becomes the bytes the payload had, the blob is left alone
        assert _restored(value, {'a\ufffdb': b'a\xffb'}) == {
            'content': msgspec.Raw(b'a\xffb'),
            'blob': b'\x00a\xef\xbf\xbdb\x00',
        }

    def test_one_rewritten_item_is_restored(self):
        value = {'content': 'a\ufffdb', 'n': 1}

        assert _restored(value, {'a\ufffdb': b'a\xffb'}) == {
            'content': msgspec.Raw(b'a\xffb'), 'n': 1}

    def _load_with_good_payload(self, nested):
        payload = (
            b'\x82\xa4good\x82\xa7payload' + nested + b'\xa1n\x01'
            b'\xa3bad\x82\xa7payload\xa1x\xa1n\xa3bad'
        )
        result, errors = load_msgpack_with_default(payload, Dict[str, RawReq], guess_default=True)

        assert [e.loc for e in errors] == [('bad', 'n')]
        return result


class TestMultipleErrorsBehindRaw:
    """A raw field must not stop the repair from reaching a second error.

    `convert` rejects the raw field right away, so the repair has to fall back
    to a typed decode of the re-encoded object. That decode still fails on the
    second error, which used to end the repair with `INPUT_REJECTED`.
    """

    class Msg(Struct):
        payload: Raw
        a: int
        b: int

    def test_both_errors_are_repaired(self):
        result, errors = load_json_with_default(
            b'{"payload": [1], "a": "x", "b": "y"}', self.Msg, guess_default=True)

        assert isinstance(result, self.Msg)
        assert result.a == 0
        assert result.b == 0
        assert bytes(result.payload) == b'[1]'
        assert [e.loc for e in errors] == [('a',), ('b',)]

    def test_the_raw_field_is_not_reported_as_rejected(self):
        result, errors = load_json_with_default(
            b'{"payload": [1], "a": "x", "b": "y"}', self.Msg, guess_default=True)

        assert ErrorType.INPUT_REJECTED not in [e.type for e in errors]

    def test_a_second_error_deeper_down_is_reached(self):
        class Inner(Struct):
            payload: Raw
            x: int
            y: int

        class Doc(Struct):
            inner: Inner
            n: int

        result, errors = load_json_with_default(
            b'{"inner": {"payload": [1], "x": "a", "y": "b"}, "n": "c"}',
            Doc, guess_default=True)

        assert [e.loc for e in errors] == [('inner', 'x'), ('inner', 'y'), ('n',)]
        assert (result.inner.x, result.inner.y, result.n) == (0, 0, 0)


class TestJsonHugeIntegerInRaw:
    """Row 10: a json raw span whose integer is too large for an untyped decode."""

    PAYLOAD = (
        b'{"good": {"payload": 1' + b'0' * 5000 + b', "n": 1}, '
        b'"bad": {"payload": 1, "n": "xx"}}'
    )

    def test_with_error_keeps_the_placeholder(self):
        obj, error = load_json_with_error(self.PAYLOAD, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert error.loc == ('...', 'n')
        assert error.msg.endswith('at `$[...].n`')

    def test_with_error_detail_gives_up(self):
        obj, detail = load_json_with_error_detail(self.PAYLOAD, Dict[str, RawReq])

        assert obj is NODEFAULT
        assert detail.loc == ('...', 'n')
        assert detail.loc_resolved is False
        assert detail.error_value_resolved is False

    def test_with_default_falls_back_to_the_root_default(self):
        """A number can not be repaired, and the whole document can not be walked.

        The error reported is the typed one: the untyped decode stops on the
        integer inside the raw field, which the typed decode never read, so its
        error is collateral. The key can not be resolved, hence the placeholder.
        """
        result, errors = load_json_with_default(self.PAYLOAD, Dict[str, RawReq], guess_default=True)

        assert result == {}
        assert [e.type for e in errors] == [ErrorType.TYPE_MISMATCH]
        assert errors[0].loc == ('...', 'n')

    def test_with_default_keeps_a_resolved_loc(self):
        """A list index needs no key, so the real error is located exactly."""

        class Event(Struct):
            kind: str
            payload: Raw

        payload = (
            b'[{"kind": "click", "payload": {"x": 1' + b'0' * 5000 + b'}}, '
            b'{"kind": 1, "payload": {}}]'
        )
        result, errors = load_json_with_default(payload, List[Event], guess_default=True)

        assert result == []
        assert [e.type for e in errors] == [ErrorType.TYPE_MISMATCH]
        assert errors[0].loc == (1, 'kind')


class TestOldMsgspecPinFallsBack:
    """Row 11: a msgspec that refuses a `Raw` instance is judged by decoding.

    Pinning a placeholder `Raw` can not settle the entry there, so the entry is
    decoded again instead: the typed decoder does take the payload bytes. Only
    this fallback is testable here, the release itself is selected by the
    installed msgspec. `tests/repair/test_malformed_loc.py` covers both steps
    with a patched `_pin_raw`.
    """

    def test_the_key_is_still_found(self, monkeypatch):
        import msgspecerror.repair as repair_mod
        from msgspecerror.repair import _find_failing_dict_value

        monkeypatch.setattr(repair_mod, '_pin_raw', lambda value, loc, model: value)
        obj = {'one': {'payload': {'a': 1}, 'n': 1}, 'two': {'payload': {'b': 2}, 'n': 'bad'}}

        assert _find_failing_dict_value(obj, RawReq, {}) == 'two'

    def test_an_entry_whose_only_issue_is_raw_is_not_the_failing_one(self, monkeypatch):
        import msgspecerror.repair as repair_mod
        from msgspecerror.repair import _find_failing_dict_value

        monkeypatch.setattr(repair_mod, '_pin_raw', lambda value, loc, model: value)
        obj = {'one': {'payload': {'a': 1}, 'n': 1}}

        assert _find_failing_dict_value(obj, RawReq, {}) is NODEFAULT


class TestJsonOrdinaryInvalidUtf8:
    """Control row: invalid utf-8 in a plain str field, no `Raw` in the model.

    The json untyped decode fails on the byte, so the loader rewrites it first.
    The located value is withdrawn when the rewrite touched it, the same way the
    msgpack family handles its own invalid utf-8.
    """

    PAYLOAD = b'{"a": 1, "b": "\xff"}'
    MODEL = Dict[str, int]

    def test_with_error_reports_a_type_mismatch(self):
        """The typed decode turns the bad byte into a str, so it reads as a mismatch."""
        obj, error = load_json_with_error(self.PAYLOAD, self.MODEL)

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('...',)

    def test_with_error_detail_locates_but_withdraws_the_value(self):
        obj, detail = load_json_with_error_detail(self.PAYLOAD, self.MODEL)

        assert obj is NODEFAULT
        assert detail.loc == ('b',)
        assert detail.loc_resolved is True
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_a_clean_value_is_not_withdrawn(self):
        """The bad byte is elsewhere, so the located value is the real one."""
        obj, detail = load_json_with_error_detail(b'{"a": "x", "b": "\xff"}', self.MODEL)

        assert detail.loc == ('a',)
        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_with_default_deletes_only_the_bad_entry(self):
        result, errors = load_json_with_default(self.PAYLOAD, self.MODEL)

        assert result == {'a': 1}
        assert [e.loc for e in errors] == [('b',), ('b',)]
        assert errors[0].type is ErrorType.UNICODE_DECODE_ERROR
        assert errors[1].type is ErrorType.TYPE_MISMATCH

    def test_with_default_guesses_only_the_bad_entry(self):
        result, errors = load_json_with_default(self.PAYLOAD, self.MODEL, guess_default=True)

        assert result == {'a': 1, 'b': 0}
        assert errors[0].type is ErrorType.UNICODE_DECODE_ERROR

    def test_a_lone_surrogate_can_not_be_located_at_all(self):
        """A str payload is encoded whole, so the error has no field to point at."""
        payload = '{"a": 1, "b": "' + '\ud800' + '"}'

        obj, error = load_json_with_error(payload, self.MODEL)
        assert error.type is ErrorType.UNICODE_DECODE_ERROR
        assert error.loc == ()

        detail_obj, detail = load_json_with_error_detail(payload, self.MODEL)
        assert detail.loc == ()
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

        default_obj, errors = load_json_with_default(payload, self.MODEL)
        assert default_obj == {'a': 1}
        assert errors[0].type is ErrorType.UNICODE_DECODE_ERROR


class TestMsgpackOrdinaryInvalidUtf8:
    """Control row: invalid utf-8 in a plain str field, no `Raw` in the model.

    Unlike json, the byte repair can locate the failing key, so
    `_with_error_detail` reports `('b',)` while the value stays withdrawn.
    """

    PAYLOAD = b'\x82\xa1a\x01\xa1b\xa1\xff'
    MODEL = Dict[str, int]

    def test_with_error_reports_a_type_mismatch(self):
        obj, error = load_msgpack_with_error(self.PAYLOAD, self.MODEL)

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('...',)

    def test_with_error_detail_locates_but_withdraws_the_value(self):
        obj, detail = load_msgpack_with_error_detail(self.PAYLOAD, self.MODEL)

        assert obj is NODEFAULT
        assert detail.loc == ('b',)
        assert detail.loc_resolved is True
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_with_default_deletes_only_the_bad_entry(self):
        result, errors = load_msgpack_with_default(self.PAYLOAD, self.MODEL)

        assert result == {'a': 1}
        assert [e.loc for e in errors] == [('b',)]

    def test_with_default_guesses_only_the_bad_entry(self):
        result, errors = load_msgpack_with_default(self.PAYLOAD, self.MODEL, guess_default=True)

        assert result == {'a': 1, 'b': 0}
        assert [e.loc for e in errors] == [('b',)]

    def test_the_bad_byte_inside_the_failing_value_is_handled_the_same(self):
        obj, detail = load_msgpack_with_error_detail(b'\x82\xa1a\xa1\xff\xa1b\x01', self.MODEL)

        assert detail.loc == ('a',)
        assert detail.loc_resolved is True
        assert detail.error_value is UNSET


class TestOldMsgspecBuildFallback:
    """The pre-0.19.0 branch, forced on a newer msgspec.

    `convert` refuses a `Raw` instance on those versions, so the repaired object
    can only become a model by being encoded and decoded again. The fallback is
    selected by `MSGSPEC_0_19_PLUS`, read at call time, so it can be forced
    here instead of installing an old msgspec.
    """

    class Msg(Struct):
        payload: Raw
        a: int
        b: int

    def test_a_repaired_struct_is_built_by_decoding_again(self, monkeypatch):
        monkeypatch.setattr(msgspecerror.const, 'MSGSPEC_0_19_PLUS', False)

        result, errors = load_json_with_default(
            b'{"payload": [1], "a": "x", "b": 2}', self.Msg, guess_default=True)

        assert isinstance(result, self.Msg)
        assert result.a == 0
        assert result.b == 2
        assert bytes(result.payload) == b'[1]'
        assert [e.loc for e in errors] == [('a',)]

    def test_the_rebuilt_value_is_not_reported_as_changed(self, monkeypatch):
        monkeypatch.setattr(msgspecerror.const, 'MSGSPEC_0_19_PLUS', False)

        result, errors = load_json_with_default(
            b'{"payload": [1], "a": 1, "b": 2}', self.Msg)

        assert result.a == 1
        assert errors == []

    def test_an_in_memory_object_is_rebuilt_through_msgpack(self, monkeypatch):
        """No payload, so the object is encoded as msgpack and decoded again."""
        monkeypatch.setattr(msgspecerror.const, 'MSGSPEC_0_19_PLUS', False)

        result, errors = convert_with_default({'payload': {'a': 1}, 'a': 1, 'b': 2}, self.Msg)

        assert isinstance(result, self.Msg)
        assert msgspec.msgpack.decode(result.payload) == {'a': 1}
        assert errors == []

    def test_a_raw_field_after_the_real_error_is_rebuilt(self, monkeypatch):
        """The raw field is only reached after `a` is repaired, then re-encoded."""

        class Late(Struct):
            a: int
            payload: Raw

        monkeypatch.setattr(msgspecerror.const, 'MSGSPEC_0_19_PLUS', False)

        result, errors = convert_with_default(
            {'a': 'bad', 'payload': {'x': 1}}, Late, guess_default=True)

        assert result.a == 0
        assert msgspec.msgpack.decode(result.payload) == {'x': 1}
        assert [e.loc for e in errors] == [('a',)]


class TestWithErrorIsFaithful:
    """The whole `*_with_error` column: a faithful `parse_msgspec_error` passthrough.

    This family never resolves `...`, so its only failure mode would be a parse
    that disagrees with msgspec. Every table row is checked against the error
    msgspec raised, field by field, and the location is rendered back into a
    `$.a[0]` path to compare with the path inside msgspec's own message.
    """

    ARR = ArrRaw

    @staticmethod
    def _render(loc):
        path = '$'
        for part in loc:
            if isinstance(part, int):
                path += f'[{part}]'
            elif part in ('...', '...key'):
                path += '[...]'
            else:
                path += f'.{part}'
        return path

    @pytest.mark.parametrize('payload, model', [
        # Raw required, the real error is another field
        (b'{"one": {"payload": 1, "n": 1}, "two": {"payload": 2, "n": "bad"}}', Dict[str, RawReq]),
        # Raw with a default
        (b'{"one": {"payload": {"a": 1}, "n": 1}, "two": {"n": "bad"}}', Dict[str, RawDefault]),
        # a plain invalid utf-8 byte
        (b'{"a": 1, "b": "\xff"}', Dict[str, int]),
        # truncated payload
        (b'{"a": 1, "b"', Dict[str, int]),
        # nested placeholders
        (b'{"a": {"b": {"c": "x"}}}', Dict[str, Dict[str, Dict[str, int]]]),
        # root
        (b'{"a": "x"}', int),
    ])
    def test_json_matches_the_msgspec_error(self, payload, model):
        try:
            msgspec.json.decode(payload, type=model)
        except Exception as e:
            direct = parse_msgspec_error(e)
        else:
            raise AssertionError('the payload should fail')

        obj, error = load_json_with_error(payload, model)

        assert obj is NODEFAULT
        assert (error.msg, error.type, error.loc, error.ctx) == \
               (direct.msg, direct.type, direct.loc, direct.ctx)

    @pytest.mark.parametrize('payload, model', [
        (msgspec.msgpack.encode({'a': 'bad'}), Dict[str, int]),
        (b'\x82\xa1a\x01\xa1b\xa1\xff', Dict[str, int]),
        (msgspec.msgpack.encode({'k': [1, 'x']}), Dict[str, List[int]]),
    ])
    def test_msgpack_matches_the_msgspec_error(self, payload, model):
        try:
            msgspec.msgpack.decode(payload, type=model)
        except Exception as e:
            direct = parse_msgspec_error(e)
        else:
            raise AssertionError('the payload should fail')

        obj, error = load_msgpack_with_error(payload, model)

        assert obj is NODEFAULT
        assert (error.msg, error.type, error.loc, error.ctx) == \
               (direct.msg, direct.type, direct.loc, direct.ctx)

    @pytest.mark.parametrize('data, model', [
        # a Raw field is reported where `convert` stops, not where the data is wrong
        ({'payload': {'a': 1}, 'n': 1}, RawReq),
        ({'v': {'a': 1}}, 'Union[Raw, int]'),
    ])
    def test_convert_matches_the_msgspec_error(self, data, model):
        model = Union[Raw, int] if model == 'Union[Raw, int]' else model
        try:
            msgspec.convert(data, type=model)
        except Exception as e:
            direct = parse_msgspec_error(e)
        else:
            raise AssertionError('the data should fail')

        obj, error = convert_with_error(data, model)

        assert obj is NODEFAULT
        assert (error.msg, error.type, error.loc, error.ctx) == \
               (direct.msg, direct.type, direct.loc, direct.ctx)

    def test_array_like_raw_loc_is_the_index_msgspec_reported(self):
        """The faithful answer for a struct index is `('...', 1)`, not a key."""
        payload = b'{"one": [{"a": 1}, 1], "two": [{"b": 2}, "bad"]}'

        obj, error = load_json_with_error(payload, Dict[str, self.ARR])

        assert obj is NODEFAULT
        assert error.loc == ('...', 1)
        assert error.msg.endswith('at `$[...][1]`')
        assert self._render(error.loc) == '$[...][1]'


class TestRawDeferredDecoding:
    """The documented use of `Raw`: keep a payload undecoded until its type is known.

    msgspec: "Fields annotated with the Raw type won't be decoded immediately, but
    will instead return a Raw object with a view into the original message...
    useful for decoding fields whose type may only be inferred after decoding
    other fields."

    The content is valid here, that is the point: nothing in the payload is
    wrong, only a field beside it. These tests pin what the repair families do to
    a raw payload that a caller still intends to decode later.
    """

    class Event(Struct):
        kind: str
        payload: Raw

    DOC = b'[{"kind": "click", "payload": {"x": 1,   "y": [1,2]}}]'
    PAIRED = b'[{"kind": "click", "payload": {"x": 1,   "y": [1,2]}}, {"kind": 1, "payload": {}}]'

    def test_a_clean_payload_is_a_view_of_the_original_bytes(self):
        result, errors = load_json_with_default(self.DOC, List[self.Event])

        assert errors == []
        assert bytes(result[0].payload) == b'{"x": 1,   "y": [1,2]}'

    def test_the_payload_decodes_with_the_type_its_sibling_selects(self):
        """The pattern the docs describe: `kind` decides how `payload` is read."""
        doc = b'[{"kind": "click", "payload": {"x": 1, "y": 2}}]'
        result, errors = load_json_with_default(doc, List[self.Event])

        assert errors == []
        assert msgspec.json.decode(result[0].payload) == {'x': 1, 'y': 2}

    def test_json_loses_the_formatting_but_not_the_value(self):
        """A repair anywhere re-encodes the document, so the span is rewritten."""
        result, errors = load_json_with_default(self.PAIRED, List[self.Event], guess_default=True)

        assert [e.loc for e in errors] == [(1, 'kind')]
        assert bytes(result[0].payload) == b'{"x":1,"y":[1,2]}'
        assert msgspec.json.decode(result[0].payload) == {'x': 1, 'y': [1, 2]}
        assert bytes(result[0].payload) != b'{"x": 1,   "y": [1,2]}'

    def test_msgpack_keeps_the_bytes_exactly(self):
        """msgpack has no formatting, so re-encoding is identity."""
        good = msgspec.msgpack.encode([{'kind': 'click', 'payload': {'x': 1, 'y': [1, 2]}}])
        original = bytes(msgspec.msgpack.decode(good, type=List[self.Event])[0].payload)

        doc = msgspec.msgpack.encode([
            {'kind': 'click', 'payload': {'x': 1, 'y': [1, 2]}},
            {'kind': 1, 'payload': {}},
        ])
        result, errors = load_msgpack_with_default(doc, List[self.Event], guess_default=True)

        assert [e.loc for e in errors] == [(1, 'kind')]
        assert bytes(result[0].payload) == original
        assert msgspec.msgpack.decode(result[0].payload) == {'x': 1, 'y': [1, 2]}

    def test_the_repaired_entry_still_decodes_lazily(self):
        result, errors = load_json_with_default(self.PAIRED, List[self.Event], guess_default=True)

        assert msgspec.json.decode(result[0].payload) == {'x': 1, 'y': [1, 2]}
        assert msgspec.json.decode(result[1].payload) == {}

    def test_no_input_rejected_when_a_raw_field_is_refilled(self):
        result, errors = load_json_with_default(self.PAIRED, List[self.Event], guess_default=True)

        assert ErrorType.INPUT_REJECTED not in [e.type for e in errors]
        assert all(isinstance(item.payload, Raw) for item in result)

    @pytest.mark.parametrize('payload, expected', [
        # a scalar and a string have one canonical form, the span is unchanged
        (b'42', b'42'),
        (b'"{\\"a\\": 1}"', b'"{\\"a\\": 1}"'),
        (b'{}', b'{}'),
    ])
    def test_shapes_that_survive_the_re_encoding(self, payload, expected):
        doc = b'[{"kind": "click", "payload": ' + payload + b'}, {"kind": 1, "payload": {}}]'
        result, errors = load_json_with_default(doc, List[self.Event], guess_default=True)

        assert [e.loc for e in errors] == [(1, 'kind')]
        assert bytes(result[0].payload) == expected

    def test_a_duplicate_key_keeps_the_last_value(self):
        """Known limitation: the re-encoding drops the shadowed key, the value stays."""
        doc = b'[{"kind": "click", "payload": {"a": 1, "a": 2}}, {"kind": 1, "payload": {}}]'
        result, errors = load_json_with_default(doc, List[self.Event], guess_default=True)

        assert [e.loc for e in errors] == [(1, 'kind')]
        assert bytes(result[0].payload) == b'{"a":2}'
        assert msgspec.json.decode(result[0].payload) == {'a': 2}

    def test_unicode_escapes_are_expanded(self):
        """Known limitation: the re-encoding writes the character, not the escape."""
        doc = b'[{"kind": "click", "payload": {"s": "\\u4f60"}}, {"kind": 1, "payload": {}}]'
        result, errors = load_json_with_default(doc, List[self.Event], guess_default=True)

        assert [e.loc for e in errors] == [(1, 'kind')]
        assert msgspec.json.decode(result[0].payload) == {'s': '\u4f60'}
        assert b'\\u4f60' not in bytes(result[0].payload)

    def test_the_error_families_agree_on_the_location(self):
        """`*_with_error_detail` points at the same entry the repair changes."""
        obj, detail = load_json_with_error_detail(self.PAIRED, List[self.Event])

        assert obj is NODEFAULT
        assert detail.loc == (1, 'kind')
        assert detail.error_value == 1


class TestDecoderSideRawError:
    """
    A raw-shaped error from the typed decoder is a real error.

    A required `TypedDict` field, and a dataclass field with a
    `default_factory`, carry `MS_EXTRA_FLAG`. The decoder does not take its raw
    shortcut for them, so it fails on the field itself and prints the same type
    name a `convert` artifact prints. Only the direction tells the two apart.
    """

    class Required(TypedDict):
        payload: Raw
        n: int

    DOC = b'{"one": {"payload": {"a": 1}, "n": 1}}'

    def test_the_decoder_error_is_kept(self):
        obj, error = load_json_with_error(self.DOC, Dict[str, self.Required])

        assert obj is NODEFAULT
        assert error.type is ErrorType.TYPE_MISMATCH
        assert error.loc == ('...', 'payload')

    def test_the_input_is_not_rejected_as_a_raw_model(self):
        """The repair must not spin on a field the decoder itself refused."""
        result, errors = load_json_with_default(self.DOC, Dict[str, self.Required])

        assert [e.type for e in errors] == [ErrorType.TYPE_MISMATCH]


@needs_convert_raw
class TestNonStructRawFamilies:
    """
    A raw field of a TypedDict / dataclass / NamedTuple is a known gap.

    The walk only knows msgspec Struct fields, so the entry at such a raw field
    can not be judged: `_with_error_detail` stops at the placeholder and
    `_with_default` drops the whole document. The Struct row of ?3.9.1 is the
    contract these tests hold to, and they are `xfail(strict=True)` until the
    walk learns the model kinds.
    """

    DOC = b'{"one": {"payload": {"a": 1}, "n": 1}, "two": {"payload": {"b": 2}, "n": "bad"}}'
    NT_DOC = b'{"one": [{"a": 1}, 1], "two": [{"b": 2}, "bad"]}'

    CASES = [
        pytest.param(TDRecord, 'DOC', ('two', 'n'), id='typed-dict'),
        pytest.param(DCRecord, 'DOC', ('two', 'n'), id='dataclass'),
        pytest.param(NTRecord, 'NT_DOC', ('two', 1), id='named-tuple'),
    ]

    @pytest.mark.xfail(reason='the walk does not know plain model fields', strict=True)
    @pytest.mark.parametrize('model, doc_name, bad_loc', CASES)
    def test_with_error_detail_resolves_the_failing_field(self, model, doc_name, bad_loc):
        doc = getattr(self, doc_name)
        obj, detail = load_json_with_error_detail(doc, Dict[str, model])

        assert obj is NODEFAULT
        assert detail.loc == bad_loc
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    @pytest.mark.xfail(reason='the walk does not know plain model fields', strict=True)
    @pytest.mark.parametrize('model, doc_name, bad_loc', CASES)
    def test_with_default_repairs_only_the_bad_field(self, model, doc_name, bad_loc):
        doc = getattr(self, doc_name)
        result, errors = load_json_with_default(doc, Dict[str, model])

        assert [e.loc for e in errors] == [bad_loc]
        assert 'one' in result

    @pytest.mark.parametrize('model, doc_name, bad_loc', CASES)
    def test_convert_with_default_never_writes_a_placeholder(self, model, doc_name, bad_loc):
        """These models are a known gap, but a refusal never corrupts the input."""
        data = msgspec.json.decode(getattr(self, doc_name))
        before = copy.deepcopy(data)

        result, errors = convert_with_default(data, Dict[str, model])

        assert errors
        assert data == before
