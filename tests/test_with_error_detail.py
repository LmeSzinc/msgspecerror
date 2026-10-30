"""Tests for the `*_with_error_detail` family: first error, resolved location."""
import copy
from typing import Dict, List, Optional, Tuple, Union

import msgspec
import pytest
from msgspec import NODEFAULT, UNSET, Struct, ValidationError, convert

import msgspecerror.with_error as with_error_mod
from msgspecerror import (
    ErrorInfoDetail, convert_with_error, convert_with_error_detail, load_json_with_error_detail,
    load_msgpack_with_error_detail, parse_msgspec_error
)
from msgspecerror.const import MSGSPEC_0_19_PLUS, ErrorType
from msgspecerror.parse_error import _has_placeholder
from msgspecerror.repair import load_msgpack_with_default

# --- Test Models ---


class Req(Struct, forbid_unknown_fields=True):
    a: int
    b: str = ''


class Box(Struct):
    inner: Req
    tag: str = 't'


class Item(Struct):
    amount: int = 0


class Inventory(Struct):
    items: Dict[str, Item] = {}


# --- Realistic task definition, nested dict / union / list ---


class Group(Struct, forbid_unknown_fields=True):
    task: str = ''
    group: str = ''
    model: str = ''


class Card(Struct, forbid_unknown_fields=True):
    info: str = ''
    group: str = ''
    task: str = ''


class Task(Struct, forbid_unknown_fields=True):
    groups: Optional[Dict[str, Union[None, str, Group]]] = msgspec.field(default_factory=dict)
    displays: Optional[Union[str, List[List[Union[str, Card]]]]] = 'flat'
    global_bind: bool = False


TasksDefinition = Dict[str, Task]


def assert_invariants(detail):
    """The three invariants of every delivered ErrorInfoDetail."""
    assert isinstance(detail, ErrorInfoDetail)
    # A placeholder is only kept when it could not be resolved. The reverse does
    # not hold: a dict key can literally be the string `...`.
    assert detail.loc_resolved or _has_placeholder(detail.loc)
    assert detail.error_value_resolved == (detail.error_value is not UNSET)
    assert (not detail.error_value_resolved) or detail.loc_resolved


def assert_failed(result):
    obj, detail = result
    assert obj is NODEFAULT
    assert_invariants(detail)
    return detail


class TestSuccess:
    def test_convert(self):
        obj, detail = convert_with_error_detail({'a': 1}, Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_load_json(self):
        obj, detail = load_json_with_error_detail(b'{"a": 1}', Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_load_msgpack(self):
        obj, detail = load_msgpack_with_error_detail(
            msgspec.msgpack.encode({'a': 1}), Req)
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_json_decoder_is_accepted(self):
        obj, detail = load_json_with_error_detail(
            b'{"a": 1}', msgspec.json.Decoder(Req))
        assert obj == Req(a=1)
        assert detail is NODEFAULT

    def test_msgpack_decoder_is_accepted(self):
        obj, detail = load_msgpack_with_error_detail(
            msgspec.msgpack.encode({'a': 1}), msgspec.msgpack.Decoder(Req))
        assert obj == Req(a=1)
        assert detail is NODEFAULT


class TestConvert:
    @pytest.mark.parametrize('model, data, loc, value', [
        (Req, {'a': 'bad'}, ('a',), 'bad'),
        (Req, {'a': 1, 'z': 2}, ('z',), 2),
        (Req, {'a': [], 'b': 'x'}, ('a',), []),
        (Dict[str, int], {'k': 'x'}, ('k',), 'x'),
        (Dict[str, int], {1: 'x'}, (1,), 1),
        (Dict[str, List[int]], {'k': [1, 'x']}, ('k', 1), 'x'),
        (Dict[str, Dict[str, int]], {'a': {'b': 'x'}}, ('a', 'b'), 'x'),
        (Inventory, {'items': {'apple': {'amount': 'ABC'}}},
         ('items', 'apple', 'amount'), 'ABC'),
        # A dict key that is literally the placeholder text is a real key
        (Dict[str, int], {'...': 'x'}, ('...',), 'x'),
        (Dict[str, int], {'...key': 'x'}, ('...key',), 'x'),
    ])
    def test_resolved(self, model, data, loc, value):
        detail = assert_failed(convert_with_error_detail(data, model))
        assert detail.loc == loc
        assert detail.error_value == value
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_missing_field(self):
        detail = assert_failed(convert_with_error_detail({}, Req))
        assert detail.loc == ('a',)
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_root_error(self):
        detail = assert_failed(convert_with_error_detail('abc', int))
        assert detail.loc == ()
        assert detail.error_value == 'abc'

    def test_decoder_equals_model(self):
        from_model = assert_failed(convert_with_error_detail({'k': 'x'}, Dict[str, int]))
        from_decoder = assert_failed(convert_with_error_detail(
            {'k': 'x'}, msgspec.json.Decoder(Dict[str, int])))
        assert from_decoder.loc == from_model.loc
        assert from_decoder.error_value == from_model.error_value

    def test_does_not_modify_data(self):
        data = {'items': {'apple': {'amount': 'ABC'}}}
        snapshot = copy.deepcopy(data)
        assert_failed(convert_with_error_detail(data, Inventory))
        assert data == snapshot


class TestJsonPayload:
    @pytest.mark.parametrize('payload, loc, value, loc_resolved', [
        (b'{"a": "bad"}', ('a',), 'bad', True),
        # The later byte is not valid utf-8, the failing value itself is intact
        (b'{"a": "bad", "b": "\xff"}', ('a',), 'bad', True),
        (b'{"a": "bad", "b": 1', ('a',), UNSET, True),
        (b'{"a": ', (), UNSET, True),
        ('{"a": "bad"}', ('a',), 'bad', True),
    ])
    def test_req(self, payload, loc, value, loc_resolved):
        detail = assert_failed(load_json_with_error_detail(payload, Req))
        assert detail.loc == loc
        assert detail.error_value is value or detail.error_value == value
        assert detail.loc_resolved is loc_resolved

    def test_str_lone_surrogate(self):
        """
        msgspec encodes a str payload to utf-8 before parsing it, so the lone
        surrogate fails on the whole payload: the error is a codec error, not
        a validation error at a field, and the location is empty.
        """
        payload = '{"a": "bad", "b": "' + '\ud800' + '"}'
        detail = assert_failed(load_json_with_error_detail(payload, Req))
        assert detail.loc == ()
        assert detail.loc_resolved is True
        assert detail.type is ErrorType.UNICODE_DECODE_ERROR
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_literal_placeholder_key(self):
        """A dict key that is literally `...` is a real key, not a placeholder."""
        detail = assert_failed(load_json_with_error_detail(b'{"...": "x"}', Dict[str, int]))
        assert detail.loc == ('...',)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('payload, loc, loc_resolved', [
        (b'{"k": "x"}', ('k',), True),
        (b'{"k": "x", "b": "\xff"}', ('k',), True),
        (b'{"k": "x", "b": 1', ('...',), False),
        (b'{"k": "x"} {"j": 1}', ('...',), False),
    ])
    def test_dict_model(self, payload, loc, loc_resolved):
        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, int]))
        assert detail.loc == loc
        assert detail.loc_resolved is loc_resolved
        assert detail.error_value_resolved is (loc_resolved and loc == ('k',))


class TestMsgpackPayload:
    @pytest.mark.parametrize('payload, loc, value, loc_resolved', [
        (msgspec.msgpack.encode({'a': 'bad'}), ('a',), 'bad', True),
        # The later byte is not valid utf-8: an untyped decode rejects it, but
        # the failing value itself is intact, so the location is still resolved
        (b'\x82\xa1a\xa3bad\xa1b\xa1\xff', ('a',), 'bad', True),
        (b'\x82\xa1a\xa3bad', ('a',), UNSET, True),
        (b'\xc1', (), UNSET, True),
    ])
    def test_req(self, payload, loc, value, loc_resolved):
        detail = assert_failed(load_msgpack_with_error_detail(payload, Req))
        assert detail.loc == loc
        assert detail.error_value is value or detail.error_value == value
        assert detail.loc_resolved is loc_resolved

    @pytest.mark.parametrize('payload, loc, loc_resolved', [
        (msgspec.msgpack.encode({'k': 'x'}), ('k',), True),
        (b'\x82\xa1k\xa1x\xa1b\xa1\xff', ('k',), True),
        (b'\x82\xa1k\xa1x\xa1b', ('...',), False),
    ])
    def test_dict_model(self, payload, loc, loc_resolved):
        detail = assert_failed(load_msgpack_with_error_detail(payload, Dict[str, int]))
        assert detail.loc == loc
        assert detail.loc_resolved is loc_resolved

    @pytest.mark.parametrize('key', ['...', '...key'])
    def test_literal_placeholder_key(self, key):
        """A dict key that is literally the placeholder text is a real key."""
        payload = msgspec.msgpack.encode({key: 'x'})
        detail = assert_failed(load_msgpack_with_error_detail(payload, Dict[str, int]))
        assert detail.loc == (key,)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True


class TestSecondDecodeFailure:
    """The typed decode stops early, the raw one may still fail."""

    def test_json_invalid_byte_is_repaired_for_locating(self):
        """The invalid byte is rewritten, so the failing key can still be found."""
        detail = assert_failed(
            load_json_with_error_detail(b'{"k": "x", "b": "\xff"}', Dict[str, int]))
        assert detail.loc == ('k',)
        assert detail.loc_resolved is True
        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_json_truncated_payload_keeps_the_placeholder(self):
        """A truncated document can not be repaired, the location stays a guess."""
        detail = assert_failed(
            load_json_with_error_detail(b'{"k": "x", "b": 1', Dict[str, int]))
        assert detail.loc == ('...',)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_json_concrete_loc_keeps_the_value(self):
        """`$.a` is concrete already, and its value was not touched by the repair."""
        detail = assert_failed(
            load_json_with_error_detail(b'{"a": "bad", "b": "\xff"}', Req))
        assert detail.loc == ('a',)
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_msg_type_is_preserved(self):
        detail = assert_failed(
            load_json_with_error_detail(b'{"k": "x", "b": "\xff"}', Dict[str, int]))
        assert detail.msg.startswith('Expected `int`, got `str`')

    @pytest.mark.skipif(
        not MSGSPEC_0_19_PLUS,
        reason='msgspec < 0.19.0 refuses a Raw instance in convert, the key can not be located',
    )
    def test_msgpack_invalid_utf8_is_repaired_for_locating(self):
        """An invalid byte outside the failing value does not block the key.

        A `msgspec.Raw` field keeps the payload bytes, so the typed decode reads
        a string that an untyped decode rejects. The bytes are repaired only to
        find the key, the value reported here never saw the repair.
        """
        bad_str = b'\xa3a\xffb'
        good = b'\x82\xa7payload' + bad_str + b'\xa1n\x01'
        bad = b'\x82\xa7payload\xa1x\xa1n\xa3bad'
        payload = b'\x82\xa4good' + good + b'\xa3bad' + bad

        class WithRaw(Struct):
            payload: msgspec.Raw
            n: int

        detail = assert_failed(load_msgpack_with_error_detail(payload, Dict[str, WithRaw]))
        assert detail.loc == ('bad', 'n')
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    def test_a_repaired_value_is_not_handed_back(self):
        """The guard on the located value: a U+FFFD means the bytes were rewritten."""
        from msgspecerror.with_error import _contains_replacement

        assert _contains_replacement('a\ufffdb') is True
        assert _contains_replacement({'k': 'a\ufffdb'}) is True
        assert _contains_replacement(['ok', {'k': 'a\ufffdb'}]) is True
        assert _contains_replacement('plain') is False
        assert _contains_replacement({'k': 1}) is False
        assert _contains_replacement(UNSET) is False

    def test_json_integer_out_of_range_keeps_the_placeholder(self):
        """A number too large for an untyped decode can not be walked.

        The typed decode accepts it inside a `msgspec.Raw` field, the untyped
        one rejects it, and there is no way to repair a number, so the location
        is left as msgspec reported it.
        """
        big = b'1' + b'0' * 5000
        payload = (b'{"good": {"payload": ' + big + b', "n": 1}, '
                   b'"bad": {"payload": 1, "n": "xx"}}')

        class WithRaw(Struct):
            payload: msgspec.Raw
            n: int

        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, WithRaw]))
        assert detail.loc == ('...', 'n')
        assert detail.loc_resolved is False
        assert detail.error_value_resolved is False

    def test_a_literal_key_is_distinguishable_from_an_unresolved_one(self):
        """Both locations read `('...',)`, the flag tells them apart."""
        resolved = assert_failed(
            load_json_with_error_detail(b'{"...": "x"}', Dict[str, int]))
        unresolved = assert_failed(
            load_json_with_error_detail(b'{"...": "x", "b": 1', Dict[str, int]))

        assert resolved.loc == unresolved.loc == ('...',)
        assert resolved.loc_resolved is True
        assert resolved.error_value_resolved is True
        assert unresolved.loc_resolved is False
        assert unresolved.error_value_resolved is False


class TestCallerBugRaises:
    """A decoder of the other format is a bug, not bad data, so it is not converted."""

    def test_json_rejects_msgpack_decoder(self):
        with pytest.raises(
                TypeError,
                match=r'load_json_with_error_detail\(\) requires a msgspec\.json\.Decoder, '
                      r'got a msgspec\.msgpack\.Decoder'):
            load_json_with_error_detail(b'{"a": 1}', msgspec.msgpack.Decoder(Req))

    def test_msgpack_rejects_json_decoder(self):
        with pytest.raises(
                TypeError,
                match=r'load_msgpack_with_error_detail\(\) requires a msgspec\.msgpack\.Decoder, '
                      r'got a msgspec\.json\.Decoder'):
            load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 1}), msgspec.json.Decoder(Req))


class TestValidationOptions:
    """The key search validates with the rules the failed call used.

    Validation rules decide which entry fails, so a key searched with other
    rules points at the wrong entry: `strict=False` accepts the numeric string
    `'1'` as an int, and a json payload coerces string dict keys.
    """

    def test_strict_false_decoder_reports_the_real_key(self):
        class Inner(Struct):
            a: int

        decoder = msgspec.json.Decoder(Dict[str, Inner], strict=False)
        payload = b'{"one": {"a": "1"}, "two": {"a": "bad"}}'

        # `one` is accepted by the decoder, only `two` fails
        assert decoder.decode(b'{"one": {"a": "1"}}') == {'one': Inner(a=1)}
        detail = assert_failed(load_json_with_error_detail(payload, decoder))
        assert detail.loc == ('two', 'a')
        assert detail.error_value == 'bad'

    def test_strict_true_decoder_reports_the_first_key(self):
        """Without the coercion the first entry fails, so it is the reported one."""
        class Inner(Struct):
            a: int

        decoder = msgspec.json.Decoder(Dict[str, Inner])
        payload = b'{"one": {"a": "1"}, "two": {"a": "bad"}}'
        detail = assert_failed(load_json_with_error_detail(payload, decoder))
        assert detail.loc == ('one', 'a')
        assert detail.error_value == '1'

    def test_json_dict_keys_are_coerced(self):
        class Inner(Struct):
            d: Dict[int, int] = {}

        payload = b'{"d": {"1": 5, "x": 1}}'
        # `'1'` is a valid int key of a json payload, `'x'` is not
        assert msgspec.json.decode(b'{"d": {"1": 5}}', type=Inner) == Inner(d={1: 5})
        detail = assert_failed(load_json_with_error_detail(payload, Inner))
        assert detail.loc == ('d', 'x')
        assert detail.error_value == 'x'

    def test_json_plain_dict_keys_are_coerced(self):
        detail = assert_failed(
            load_json_with_error_detail(b'{"1": 5, "x": 1}', Dict[int, int]))
        assert detail.loc == ('x',)
        assert detail.error_value == 'x'

    def test_msgpack_dict_keys_are_not_coerced(self):
        """Msgpack keys may be any type, a str key is not an int key."""
        class Inner(Struct):
            d: Dict[int, int] = {}

        payload = msgspec.msgpack.encode({'d': {'1': 5, 'x': 1}})
        detail = assert_failed(load_msgpack_with_error_detail(payload, Inner))
        assert detail.loc == ('d', '1')
        assert detail.error_value == '1'

    def test_msgpack_int_keys_pass(self):
        class Inner(Struct):
            d: Dict[int, int] = {}

        payload = msgspec.msgpack.encode({'d': {1: 5, 'x': 1}})
        detail = assert_failed(load_msgpack_with_error_detail(payload, Inner))
        assert detail.loc == ('d', 'x')
        assert detail.error_value == 'x'

    def test_convert_strict_false(self):
        class Inner(Struct):
            a: int

        data = {'one': {'a': '1'}, 'two': {'a': 'bad'}}
        detail = assert_failed(
            convert_with_error_detail(data, Dict[str, Inner], strict=False))
        assert detail.loc == ('two', 'a')
        assert detail.error_value == 'bad'

    def test_convert_str_keys(self):
        detail = assert_failed(
            convert_with_error_detail({'1': 5, 'x': 1}, Dict[int, int], str_keys=True))
        assert detail.loc == ('x',)

    def test_convert_kwargs_override_the_decoder(self):
        decoder = msgspec.json.Decoder(Dict[int, int])
        detail = assert_failed(
            convert_with_error_detail({'1': 5, 'x': 1}, decoder, str_keys=False))
        assert detail.loc == ('1',)

    def test_dec_hook_is_reused(self):
        class Custom:
            def __init__(self, v):
                self.v = v

        def dec_hook(typ, obj):
            if typ is Custom:
                return Custom(obj)
            raise NotImplementedError

        class Inner(Struct):
            c: Custom
            n: int

        model = Dict[str, Inner]
        data = {'one': {'c': 1, 'n': 1}, 'two': {'c': 2, 'n': 'bad'}}

        detail = assert_failed(convert_with_error_detail(data, model, dec_hook=dec_hook))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

        # Without the hook the first entry looks invalid, the answer is not the
        # one the caller's call would have produced
        without_hook = assert_failed(convert_with_error_detail(data, model))
        assert without_hook.loc == ('one', 'c')


@pytest.mark.skipif(
    not MSGSPEC_0_19_PLUS,
    reason='msgspec < 0.19.0 refuses a Raw instance in convert, the key can not be located',
)
class TestRawFieldModel:
    """A `msgspec.Raw` field can not be re-validated from a decoded object.

    An untyped decode turns the raw payload into an ordinary object, so the
    value the typed decoder accepted looks invalid. Such a failure is an
    artifact of validating twice, the entry is validated again with a raw value
    pinned where the error pointed, so a real error behind the raw field is
    still found.
    """

    class RawFirst(Struct):
        payload: msgspec.Raw
        n: int

    class RawLast(Struct):
        n: int
        payload: msgspec.Raw

    def test_a_raw_field_ahead_of_the_error_does_not_hide_it(self):
        payload = b'{"one": {"payload": 1, "n": 1}, "two": {"payload": 2, "n": "bad"}}'
        model = Dict[str, self.RawFirst]

        # The typed decoder accepts the raw field, the error is at `two`
        with pytest.raises(msgspec.ValidationError) as excinfo:
            msgspec.json.decode(payload, type=model)
        assert str(excinfo.value).endswith('at `$[...].n`')

        detail = assert_failed(load_json_with_error_detail(payload, model))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

    def test_a_raw_dict_pins_every_value(self):
        """A `Dict[str, Raw]` error only says `...`, every value has to be pinned."""
        class Holder(Struct):
            d: Dict[str, msgspec.Raw]
            n: int

        payload = (b'{"one": {"d": {"a": 1, "b": 2}, "n": 1}, '
                   b'"two": {"d": {"a": 3}, "n": "bad"}}')
        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, Holder]))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

    def test_a_raw_list_pins_every_item(self):
        class Holder(Struct):
            items: List[msgspec.Raw]
            n: int

        payload = (b'{"one": {"items": [{"a": 1}, {"b": 2}], "n": 1}, '
                   b'"two": {"items": [{"c": 3}], "n": "bad"}}')
        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, Holder]))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

    def test_resolves_when_the_real_error_comes_first(self):
        """The spurious raw failure is later, so the real one is still found."""
        data = {'one': {'n': 'bad', 'payload': {'a': 2}}}
        detail = assert_failed(convert_with_error_detail(data, Dict[str, self.RawLast]))

        assert detail.loc == ('one', 'n')
        assert detail.error_value == 'bad'

    def test_nested_raw_model(self):
        class Doc(Struct):
            items: Dict[str, self.RawFirst]

        payload = b'{"items": {"one": {"payload": 1, "n": 1}, "two": {"payload": 2, "n": "bad"}}}'
        detail = assert_failed(load_json_with_error_detail(payload, Doc))
        assert detail.loc == ('items', 'two', 'n')
        assert detail.error_value == 'bad'

    def test_a_model_without_raw_still_resolves(self):
        payload = b'{"one": {"n": 1}, "two": {"n": "bad"}}'

        class Plain(Struct):
            n: int

        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, Plain]))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

    def test_the_error_itself_is_at_the_raw_field(self):
        """A missing raw field is a real error, it must not be pinned away.

        Pinning only answers the `Expected `any`, got `object`` complaint of a
        re-validation. A structural error at the same field is the decoder's
        own answer and is reported as it is.
        """
        payload = b'{"one": {"n": 1}, "two": {"payload": 1, "n": 1}}'
        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, self.RawFirst]))

        assert detail.loc == ('one', 'payload')
        assert detail.type is ErrorType.MISSING_FIELD
        assert detail.error_value is UNSET
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is False

    def test_a_long_raw_list_is_settled_in_one_round(self):
        """A list of raw values is homogeneous, the whole list is pinned at once."""
        class Holder(Struct):
            items: List[msgspec.Raw]
            n: int

        one = {'items': [{'i': i} for i in range(30)], 'n': 1}
        two = {'items': [{'x': 1}], 'n': 'bad'}
        payload = msgspec.json.encode({'one': one, 'two': two})

        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, Holder]))
        assert detail.loc == ('two', 'n')
        assert detail.error_value == 'bad'

    def test_a_fixed_tuple_pins_only_the_reported_item(self):
        """A fixed tuple is not homogeneous, pinning every item would hide errors."""
        class Holder(Struct):
            pair: Tuple[msgspec.Raw, int]
            n: int

        one = {'pair': [{'a': 1}, 5], 'n': 1}
        two = {'pair': [{'b': 2}, 'bad'], 'n': 1}
        payload = msgspec.json.encode({'one': one, 'two': two})

        detail = assert_failed(load_json_with_error_detail(payload, Dict[str, Holder]))
        assert detail.loc == ('two', 'pair', 1)
        assert detail.error_value == 'bad'

    def test_an_unknown_field_is_not_mistaken_for_raw(self):
        """A failure at a field the model does not have is a real failure."""
        data = {'one': {'n': 1, 'ghost': 1}}

        class Plain(Struct, forbid_unknown_fields=True):
            n: int

        detail = assert_failed(convert_with_error_detail(data, Dict[str, Plain]))
        assert detail.loc == ('one', 'ghost')


class TestConsistencyWithRepair:
    """The resolved location must match the one the repair engine finds."""

    @pytest.mark.parametrize('model, data', [
        (Req, {'a': 'bad'}),
        (Req, {'a': 1, 'z': 2}),
        (Req, {}),
        (Dict[str, int], {'k': 'x'}),
        (Dict[str, int], {1: 'x'}),
        (Dict[str, List[int]], {'k': [1, 'x']}),
        (Dict[str, Dict[str, int]], {'a': {'b': 'x'}}),
        (Inventory, {'items': {'apple': {'amount': 'ABC'}}}),
        (Dict[str, int], {'a': 'x', 'b': 'y'}),
        (List[int], [1, 'x']),
        (Tuple[int, str], [1, 2]),
        (Dict[str, Union[str, List[int]]], {'v': [1, 'x']}),
        (Union[Dict[str, int], List[int]], {'k': 'x'}),
    ])
    def test_loc_matches(self, model, data):
        try:
            convert(data, type=model)
        except ValidationError as e:
            raw = parse_msgspec_error(e)
        else:
            raise AssertionError('data is expected to fail validation')

        detail = assert_failed(convert_with_error_detail(data, model))
        _, errors = load_msgpack_with_default(msgspec.msgpack.encode(data), model)
        # The repair engine reports one error per failing entry, the detail
        # family reports only the first one, so compare with the first error
        assert errors
        if detail.loc_resolved:
            assert detail.loc == errors[0].loc
        else:
            assert _has_placeholder(errors[0].loc)
            assert raw.loc == detail.loc

    def test_multiple_failing_entries_report_the_first(self):
        """
        The repair engine reports one error per failing entry, the detail
        family reports only the first one. They must agree on that first entry.
        """
        data = {'a': 'x', 'b': 'y'}
        detail = assert_failed(convert_with_error_detail(data, Dict[str, int]))
        _, errors = load_msgpack_with_default(
            msgspec.msgpack.encode(data), Dict[str, int])
        assert len(errors) == 2
        assert detail.loc == errors[0].loc == ('a',)
        assert detail.error_value == 'x'


class TestLocMatchesWithError:
    """When the location is concrete, both families report the same `loc`."""

    @pytest.mark.parametrize('model, data', [
        (Req, {'a': 'bad'}),
        (Req, {}),
        (Req, {'a': 1, 'z': 2}),
        (Box, {'inner': {}}),
        (Box, {'inner': {'a': 'bad'}}),
        (List[int], [1, 'x']),
        (Tuple[int, str], [1, 2]),
        (int, 'abc'),
    ])
    def test_concrete_loc_is_left_alone(self, model, data):
        _, error = convert_with_error(data, model)
        detail = assert_failed(convert_with_error_detail(data, model))

        assert not _has_placeholder(error.loc)
        assert detail.loc == error.loc

    def test_placeholder_loc_is_the_only_difference(self):
        """A `$[...]` path is the one case where the two families disagree."""
        _, error = convert_with_error({'k': 'x'}, Dict[str, int])
        detail = assert_failed(convert_with_error_detail({'k': 'x'}, Dict[str, int]))

        assert error.loc == ('...',)
        assert detail.loc == ('k',)


class TestRootError:
    """A root level validation error carries the whole document."""

    @pytest.mark.parametrize('payload, model, value', [
        (b'"x"', int, 'x'),
        (b'1', str, 1),
        (b'[1]', int, [1]),
        (b'null', int, None),
        (b'true', int, True),
    ], ids=['str-for-int', 'int-for-str', 'list-for-int', 'null-for-int', 'bool-for-int'])
    def test_json_root_error(self, payload, model, value):
        detail = assert_failed(load_json_with_error_detail(payload, model))

        assert detail.loc == ()
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True
        assert detail.error_value == value

    def test_msgpack_root_error(self):
        detail = assert_failed(
            load_msgpack_with_error_detail(msgspec.msgpack.encode('x'), int))
        assert detail.loc == ()
        assert detail.error_value == 'x'


class TestDecodeCount:
    """One typed decode, plus one untyped decode only on the failure path."""

    def test_json_success_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_json

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_json', counting)
        load_json_with_error_detail(b'{"a": 1}', Req)
        assert len(calls) == 1

    def test_json_failure_decodes_twice(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_json

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_json', counting)
        load_json_with_error_detail(b'{"a": "bad"}', Req)

        assert len(calls) == 2
        # The second decode is untyped: it has no `type` argument
        assert calls[1][1] == {}

    def test_msgpack_success_decodes_once(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_msgpack

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_msgpack', counting)
        load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 1}), Req)
        assert len(calls) == 1

    def test_msgpack_failure_decodes_twice(self, monkeypatch):
        calls = []
        real = with_error_mod.decode_msgpack

        def counting(*args, **kwargs):
            calls.append((args, kwargs))
            return real(*args, **kwargs)

        monkeypatch.setattr(with_error_mod, 'decode_msgpack', counting)
        load_msgpack_with_error_detail(msgspec.msgpack.encode({'a': 'bad'}), Req)

        assert len(calls) == 2
        assert calls[1][1] == {}


class TestRealisticModel:
    """A real task definition: a dict of structs holding dicts, unions and lists."""

    @pytest.mark.parametrize('data, loc, value', [
        # A dict value of `groups`, the whole path has two placeholders
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        # A dict key of `groups`, the offending value is the key itself
        ({'Task': {'groups': {1: None}}}, ('Task', 'groups', 1), 1),
        # A list of lists of a tagged union member
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        # The failing entry is the second task of the mapping
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_convert(self, data, loc, value):
        detail = assert_failed(convert_with_error_detail(data, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('data, loc, value', [
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_json_payload(self, data, loc, value):
        """Json keys are always str, the int key row can only go through msgpack."""
        payload = msgspec.json.encode(data)
        detail = assert_failed(load_json_with_error_detail(payload, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value

    @pytest.mark.parametrize('data, loc, value', [
        ({'Task': {'groups': {'G': 1}}}, ('Task', 'groups', 'G'), 1),
        ({'Task': {'groups': {1: None}}}, ('Task', 'groups', 1), 1),
        ({'Task': {'displays': [[{'info': 1}]]}},
         ('Task', 'displays', 0, 0, 'info'), 1),
        ({'Good': {}, 'Bad': {'global_bind': 1}}, ('Bad', 'global_bind'), 1),
    ])
    def test_msgpack_payload(self, data, loc, value):
        payload = msgspec.msgpack.encode(data)
        detail = assert_failed(load_msgpack_with_error_detail(payload, TasksDefinition))

        assert detail.loc == loc
        assert detail.error_value == value

    def test_with_error_keeps_the_placeholders(self):
        data = {'Task': {'groups': {'G': 1}}}
        _, error = convert_with_error(data, TasksDefinition)
        assert error.loc == ('...', 'groups', '...')

    def test_data_is_not_modified(self):
        data = {'Task': {'groups': {'G': 1}}}
        snapshot = copy.deepcopy(data)
        assert_failed(convert_with_error_detail(data, TasksDefinition))
        assert data == snapshot
