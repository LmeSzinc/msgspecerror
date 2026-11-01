"""`error_value` is the caller's own object, `to_log_dict()` is what a log gets.

The offending value is delivered as it was found in the data. For the `convert_*`
entries that object comes from the caller, so it can be anything: an instance of
a plain class, a `complex`, or a structure that refers to itself through a YAML
anchor. `msgspec.json.encode()` rejects all of those, which made the documented
promise "an ErrorInfo is something you can put in a log" false for exactly the
errors that are hardest to reproduce.

The raw value stays on the error: callers read `detail.error_value['amount']`,
so replacing it with text would remove the reason the detail family exists.
`to_log_dict()` is the other side of the trade-off: plain data, always
encodable, with the few values msgspec can not carry replaced by their `repr()`.

Every test asserts the whole dict, not one key of it: the contract is a fixed
set of keys with a defined value each, and a test that reads one key would not
notice a key that went missing.
"""
import datetime
import decimal
import uuid
from typing import Dict, List

from typing_extensions import Annotated

import msgspec
import pytest
from msgspec import UNSET, Struct

from msgspecerror import convert_with_error_detail, load_json_with_error_detail, parse_msgspec_error
from msgspecerror.const import ErrorType
from msgspecerror.parse_error import ErrorInfo, ErrorInfoDetail


class Req(Struct):
    a: int
    b: str = 'x'


class Opaque:
    """An object msgspec does not know how to encode."""


def detail_of(data, model):
    """The ErrorInfoDetail of a failed `convert`, the data is not touched."""
    _, detail = convert_with_error_detail(data, model)
    return detail


def log_of(data, model):
    """The log dict of a failed `convert`."""
    return detail_of(data, model).to_log_dict()


def pattern_model():
    """A regex constraint, so `ctx` carries the constraint fields too."""
    return Dict[str, Annotated[str, msgspec.Meta(pattern=r'^[0-9]+$')]]


class TestValueIsDeliveredAsItIs:
    """The raw value is not touched, the search must keep that promise."""

    @pytest.mark.parametrize('value', [
        Opaque(), 1 + 2j, b'ab', {1, 2}, decimal.Decimal('1.5'),
        datetime.datetime(2026, 1, 1), uuid.UUID(int=1), float('nan'),
    ])
    def test_the_detail_holds_the_very_object(self, value):
        data = {'k': value}
        detail = detail_of(data, Dict[str, int])

        assert detail == ErrorInfoDetail(
            msg=detail.msg, type=ErrorType.TYPE_MISMATCH, loc=('k',), ctx=detail.ctx,
            error_value=value, loc_resolved=True, error_value_resolved=True)
        assert detail.error_value is data['k']

    def test_a_caller_object_is_not_replaced_by_text(self):
        class Payload:
            amount = 'bad'

        data = {'k': Payload()}
        detail = detail_of(data, Dict[str, int])

        assert isinstance(detail.error_value, Payload)
        assert detail.error_value.amount == 'bad'


class TestPlainErrorInfo:
    """The base class answers four keys, whatever the error is."""

    def test_a_resolved_location(self):
        info = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a', 1))

        assert info.to_log_dict() == {
            'msg': 'm',
            'type': 'TYPE_MISMATCH',
            'loc': ('a', 1),
            'ctx': {},
        }

    def test_a_root_error(self):
        info = ErrorInfo(msg='m', type=ErrorType.INPUT_REJECTED)

        assert info.to_log_dict() == {
            'msg': 'm',
            'type': 'INPUT_REJECTED',
            'loc': (),
            'ctx': {},
        }

    def test_a_context_with_constraints(self):
        info = ErrorInfo(
            msg='m', type=ErrorType.NUMERIC_CONSTRAINT, loc=('a',),
            ctx=type(ErrorInfo(msg='x', type=ErrorType.TYPE_MISMATCH).ctx)(ge=18, le=99))

        assert info.to_log_dict() == {
            'msg': 'm',
            'type': 'NUMERIC_CONSTRAINT',
            'loc': ('a',),
            'ctx': {'ge': 18, 'le': 99},
        }

    def test_a_parsed_message_keeps_the_character_of_the_error(self):
        info = parse_msgspec_error('Object missing required field `amount` - at `$.items`')

        assert info.to_log_dict() == {
            'msg': 'Object missing required field `amount` - at `$.items`',
            'type': 'MISSING_FIELD',
            'loc': ('items', 'amount'),
            'ctx': {},
        }


class TestDetailLogDict:
    """The detail answers seven keys, and the value is what the rules say."""

    def test_a_string_value(self):
        assert log_of({'a': 'bad'}, Req) == {
            'msg': 'Expected `int`, got `str` - at `$.a`',
            'type': 'TYPE_MISMATCH',
            'loc': ('a',),
            'ctx': {'expected': 'int', 'got': 'str'},
            'error_value': 'bad',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_a_resolved_dict_key(self):
        assert log_of({'k': 'x', 'ok': 1}, Dict[str, int]) == {
            'msg': 'Expected `int`, got `str` - at `$[...]`',
            'type': 'TYPE_MISMATCH',
            'loc': ('k',),
            'ctx': {'expected': 'int', 'got': 'str'},
            'error_value': 'x',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_a_failing_key(self):
        """The offending value of a key error is the key itself."""
        assert log_of({'k': 'x'}, Dict[int, int]) == {
            'msg': 'Expected `int`, got `str` - at `key` in `$`',
            'type': 'TYPE_MISMATCH',
            'loc': ('k',),
            'ctx': {'expected': 'int', 'got': 'str'},
            'error_value': 'k',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_a_missing_field_has_no_value(self):
        assert log_of({}, Req) == {
            'msg': 'Object missing required field `a`',
            'type': 'MISSING_FIELD',
            'loc': ('a',),
            'ctx': {},
            'error_value': None,
            'loc_resolved': True,
            'error_value_resolved': False,
        }

    def test_a_index_error(self):
        assert log_of([1, 'x'], List[int]) == {
            'msg': 'Expected `int`, got `str` - at `$[1]`',
            'type': 'TYPE_MISMATCH',
            'loc': (1,),
            'ctx': {'expected': 'int', 'got': 'str'},
            'error_value': 'x',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_a_constraint_error_carries_its_limits(self):
        assert log_of({'k': 'nope'}, pattern_model()) == {
            'msg': "Expected `str` matching regex '^[0-9]+$' - at `$[...]`",
            'type': 'PATTERN_CONSTRAINT',
            'loc': ('k',),
            'ctx': {'expected': 'str', 'pattern': '^[0-9]+$'},
            'error_value': 'nope',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_an_unresolved_location_keeps_the_placeholder(self):
        info = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('...',))
        detail = ErrorInfoDetail(
            msg=info.msg, type=info.type, loc=info.loc, ctx=info.ctx,
            loc_resolved=False, error_value_resolved=False)

        assert detail.to_log_dict() == {
            'msg': 'm',
            'type': 'TYPE_MISMATCH',
            'loc': ('...',),
            'ctx': {},
            'error_value': None,
            'loc_resolved': False,
            'error_value_resolved': False,
        }

    def test_a_hand_built_detail_with_every_field(self):
        detail = ErrorInfoDetail(
            msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a', 0, 'b'), ctx=detail_of({}, Req).ctx,
            error_value={'deep': [1, 2]}, loc_resolved=True, error_value_resolved=True)

        assert detail.to_log_dict() == {
            'msg': 'm',
            'type': 'TYPE_MISMATCH',
            'loc': ('a', 0, 'b'),
            'ctx': {},
            'error_value': {'deep': [1, 2]},
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_the_keys_do_not_depend_on_the_error(self):
        expected_keys = {
            'msg', 'type', 'loc', 'ctx', 'error_value', 'loc_resolved', 'error_value_resolved',
        }

        for data, model in (({}, Req), ({'a': 'bad'}, Req), ({'k': 'x'}, Dict[str, int])):
            assert set(log_of(data, model)) == expected_keys


class TestTheValueRules:
    """`error_value` is converted, or replaced by text, by one fixed rule."""

    @pytest.mark.parametrize('value,got,expected', [
        (b'ab', 'bytes', 'YWI='),
        ({1, 2}, 'set', [1, 2]),
        (decimal.Decimal('1.5'), 'decimal', '1.5'),
        (datetime.datetime(2026, 1, 1), 'datetime', '2026-01-01T00:00:00'),
        (uuid.UUID(int=1), 'uuid', '00000000-0000-0000-0000-000000000001'),
        ('text', 'str', 'text'),
        (None, 'null', None),
        ([1, 2], 'array', [1, 2]),
        ({'a': 1}, 'object', {'a': 1}),
    ])
    def test_what_msgspec_can_carry_is_converted_normally(self, value, got, expected):
        detail = detail_of({'k': value}, Dict[str, int])

        assert detail.to_log_dict() == {
            'msg': f'Expected `int`, got `{got}` - at `$[...]`',
            'type': 'TYPE_MISMATCH',
            'loc': ('k',),
            'ctx': {'expected': 'int', 'got': got},
            'error_value': expected,
            'loc_resolved': True,
            'error_value_resolved': True,
        }
        assert detail.error_value is value

    def test_an_int_is_never_an_error_for_an_int_model(self):
        """The control case of the table above: a passing value locates nothing."""
        obj, detail = convert_with_error_detail({'k': 5}, Dict[str, int])

        assert obj == {'k': 5}
        assert detail is msgspec.NODEFAULT

    def test_a_float_nan_keeps_its_value(self):
        """`msgspec` writes `nan` as a bare token, the number is not touched."""
        log = log_of({'k': float('nan')}, Dict[str, int])

        assert log['error_value'] != log['error_value']  # NaN is not equal to itself
        assert set(log) == {
            'msg', 'type', 'loc', 'ctx', 'error_value', 'loc_resolved', 'error_value_resolved',
        }

    @pytest.mark.parametrize('value,got', [(Opaque(), 'Opaque'), (1 + 2j, 'complex')])
    def test_what_msgspec_can_not_carry_falls_back_to_repr(self, value, got):
        detail = detail_of({'k': value}, Dict[str, int])

        assert detail.to_log_dict() == {
            'msg': f'Expected `int`, got `{got}` - at `$[...]`',
            'type': 'TYPE_MISMATCH',
            'loc': ('k',),
            'ctx': {'expected': 'int', 'got': got},
            'error_value': repr(value),
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_a_cycle_falls_back_to_repr(self):
        """A YAML anchor produces a real cycle, which no encoder can walk."""
        cyclic = {'a': None}
        cyclic['a'] = cyclic
        detail = detail_of(cyclic, Dict[str, int])

        assert detail.to_log_dict() == {
            'msg': 'Expected `int`, got `object` - at `$[...]`',
            'type': 'TYPE_MISMATCH',
            'loc': ('a',),
            'ctx': {'expected': 'int', 'got': 'object'},
            'error_value': "{'a': {...}}",
            'loc_resolved': True,
            'error_value_resolved': True,
        }
        assert detail.error_value is cyclic

    def test_an_unset_value_is_text(self):
        """`UNSET` can not sit in a plain dict, its repr can."""
        assert log_of({'k': UNSET}, Dict[str, int]) == {
            'msg': 'Expected `int`, got `msgspec.UnsetType` - at `$[...]`',
            'type': 'TYPE_MISMATCH',
            'loc': ('k',),
            'ctx': {'expected': 'int', 'got': 'msgspec.UnsetType'},
            'error_value': 'UNSET',
            'loc_resolved': True,
            'error_value_resolved': True,
        }

    def test_an_object_that_can_not_be_represented(self):
        class Broken:
            def __repr__(self):
                raise RuntimeError('no repr here')

        assert log_of({'k': Broken()}, Dict[str, int])['error_value'] == '<Broken object>'

    def test_the_flag_tells_none_from_no_value(self):
        missing = log_of({}, Req)
        present = log_of({'a': None}, Req)

        assert missing == {
            'msg': 'Object missing required field `a`',
            'type': 'MISSING_FIELD',
            'loc': ('a',),
            'ctx': {},
            'error_value': None,
            'loc_resolved': True,
            'error_value_resolved': False,
        }
        assert present == {
            'msg': 'Expected `int`, got `null` - at `$.a`',
            'type': 'TYPE_MISMATCH',
            'loc': ('a',),
            'ctx': {'expected': 'int', 'got': 'null'},
            'error_value': None,
            'loc_resolved': True,
            'error_value_resolved': True,
        }


class TestTheLogDictIsEncodable:
    """Every dict the two classes produce goes through the json encoder."""

    VALUES = [
        ('object', Opaque()),
        ('complex', 1 + 2j),
        ('bytes', b'ab'),
        ('set', {1, 2}),
        ('decimal', decimal.Decimal('1.5')),
        ('datetime', datetime.datetime(2026, 1, 1)),
        ('uuid', uuid.UUID(int=1)),
        ('none', None),
        ('unset', UNSET),
        ('plain', 'text'),
        ('nested', {'a': [1, {'b': 2}]}),
    ]

    @pytest.mark.parametrize('name,value', VALUES, ids=[n for n, _ in VALUES])
    def test_a_detail_encodes(self, name, value):
        encoded = msgspec.json.encode(log_of({'k': value}, Dict[str, int]))

        assert encoded.startswith(b'{"msg"')

    def test_a_cycle_encodes(self):
        cyclic = {'a': None}
        cyclic['a'] = cyclic

        assert msgspec.json.encode(log_of(cyclic, Dict[str, int]))

    def test_a_hostile_repr_encodes(self):
        class Broken:
            def __repr__(self):
                raise RuntimeError('no repr here')

        assert msgspec.json.encode(log_of({'k': Broken()}, Dict[str, int]))

    def test_a_plain_error_info_encodes(self):
        info = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH)

        assert msgspec.json.encode(info.to_log_dict()) == b'{"msg":"m","type":"TYPE_MISMATCH","loc":[],"ctx":{}}'

    def test_the_wire_form_of_a_detail(self):
        assert msgspec.json.encode(log_of({'a': 'bad'}, Req)) == (
            b'{"msg":"Expected `int`, got `str` - at `$.a`","type":"TYPE_MISMATCH",'
            b'"loc":["a"],"ctx":{"expected":"int","got":"str"},'
            b'"error_value":"bad","loc_resolved":true,"error_value_resolved":true}'
        )

    def test_a_missing_field_writes_a_null_value(self):
        assert msgspec.json.encode(log_of({}, Req)) == (
            b'{"msg":"Object missing required field `a`","type":"MISSING_FIELD",'
            b'"loc":["a"],"ctx":{},"error_value":null,'
            b'"loc_resolved":true,"error_value_resolved":false}'
        )


class TestLocStaysATuple:
    """`loc` is what the error holds, not a json-shaped copy of it."""

    def test_a_plain_error_info(self):
        info = ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=('a', 0))

        assert info.to_log_dict()['loc'] is info.loc
        assert isinstance(info.to_log_dict()['loc'], tuple)

    def test_a_detail(self):
        detail = detail_of({'items': {'k': 'x'}}, Dict[str, Dict[str, int]])
        log = detail.to_log_dict()

        assert log['loc'] is detail.loc
        assert isinstance(log['loc'], tuple)

    def test_an_empty_location_stays_a_tuple(self):
        log = log_of([1, 'x'], List[int])

        assert log['loc'] == (1,)

    def test_it_still_encodes_as_an_array(self):
        detail = detail_of({'items': {'k': 'x'}}, Dict[str, Dict[str, int]])

        decoded = msgspec.json.decode(msgspec.json.encode(detail.to_log_dict()))

        assert decoded['loc'] == ['items', 'k']


class TestTheLoadFamily:
    """A decoded payload is already json-safe, the method still works."""

    def test_a_json_value(self):
        _, detail = load_json_with_error_detail(b'{"a": "bad"}', Req)

        assert detail.to_log_dict() == {
            'msg': 'Expected `int`, got `str` - at `$.a`',
            'type': 'TYPE_MISMATCH',
            'loc': ('a',),
            'ctx': {'expected': 'int', 'got': 'str'},
            'error_value': 'bad',
            'loc_resolved': True,
            'error_value_resolved': True,
        }
        assert msgspec.json.encode(detail.to_log_dict())


class TestItDoesNotTouchTheError:
    """`to_log_dict()` must not change the error it was called on."""

    def test_the_error_is_unchanged(self):
        data = {'k': Opaque()}
        detail = detail_of(data, Dict[str, int])
        before = (detail.msg, detail.type, detail.loc, detail.error_value,
                  detail.loc_resolved, detail.error_value_resolved)

        detail.to_log_dict()

        assert (detail.msg, detail.type, detail.loc, detail.error_value,
                detail.loc_resolved, detail.error_value_resolved) == before
        assert detail.error_value is data['k']

    def test_calling_it_twice_gives_the_same_answer(self):
        detail = detail_of({'k': {'a': 1 + 2j}}, Dict[str, int])

        assert detail.to_log_dict() == detail.to_log_dict()

    def test_the_dict_is_a_copy(self):
        """Writing into it must not reach the error."""
        detail = detail_of({'a': 'bad'}, Req)
        log = detail.to_log_dict()
        log['loc'] = ('mutated',)
        log['error_value'] = 'mutated'

        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'
