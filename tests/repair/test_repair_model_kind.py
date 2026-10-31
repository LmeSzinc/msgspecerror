"""Repair inside every model kind msgspec decodes into, not only ``msgspec.Struct``.

An error path names a field for a model decoded from an object (dataclass,
TypedDict, attrs) and gives a position for one decoded from an array
(NamedTuple, ``array_like=True`` struct). The walk reads the field list of
whichever kind it is holding, so a repair inside one of them lands on the field
that failed instead of the whole document.
"""
import copy
import dataclasses
from typing import Dict, List, NamedTuple, Union

import attrs
import msgspec.json
import msgspec.msgpack
import pytest
from msgspec import NODEFAULT, Struct
from typing_extensions import NotRequired, TypedDict

from msgspecerror import (
    convert_with_default, load_json_with_default, load_json_with_error_detail, load_msgpack_with_default,
    load_msgpack_with_error_detail
)
from msgspecerror.const import ErrorType

# --- Test Models ---


@dataclasses.dataclass
class Settings:
    """A dataclass nested in a struct."""
    name: str
    retries: int


class Inner(TypedDict):
    """A TypedDict nested in a struct."""
    a: int
    b: NotRequired[str]


@attrs.define
class AttrsSettings:
    """An attrs class nested in a struct."""
    name: str
    retries: int


class Pair(NamedTuple):
    """A NamedTuple is decoded from an array, so its fields are addressed by position."""
    a: int
    b: str


class Outer(Struct):
    """A struct whose fields are the three object kinds."""
    settings: Settings
    inner: Inner
    attrs_settings: AttrsSettings


class NTBox(Struct):
    """A struct holding a NamedTuple."""
    pair: Pair
    n: int


class Choice(Struct):
    """A union whose members are an object kind and an array kind."""
    kind: str
    payload: Union[Settings, List[int]]


DOC = {
    'settings': {'name': 'ok', 'retries': 1},
    'inner': {'a': 1},
    'attrs_settings': {'name': 'ok', 'retries': 1},
}


def _obj(**failing):
    """
    Build a value that is valid but for the fields given

    Args:
        **failing: `'settings.retries': 'bad'` style overrides

    Returns:
        dict: The value
    """
    obj = copy.deepcopy(DOC)
    for path, value in failing.items():
        parent, _, field = path.rpartition('.')
        target = obj
        for part in parent.split('.'):
            if not part:
                break
            target = target[part]
        target[field] = value
    return obj


def _doc(**failing):
    """
    Build a json payload that is valid but for the fields given

    Args:
        **failing: `'settings.retries': 'bad'` style overrides

    Returns:
        bytes: The payload
    """
    return msgspec.json.encode(_obj(**failing))


# --- Object kinds: the path segment is a field name ---


class TestObjectKinds:
    """A dataclass, TypedDict and attrs model are repaired like a struct."""

    CASES = [
        pytest.param(
            'settings.retries', ('settings', 'retries'), lambda r: r.settings.retries, (0, 1, 1),
            id='dataclass'),
        pytest.param(
            'inner.a', ('inner', 'a'), lambda r: r.inner['a'], (1, 0, 1),
            id='typed-dict'),
        pytest.param(
            'attrs_settings.retries', ('attrs_settings', 'retries'),
            lambda r: r.attrs_settings.retries, (1, 1, 0), id='attrs'),
    ]

    @pytest.mark.parametrize('path, bad_loc, read, expected', CASES)
    def test_with_default_repairs_only_the_bad_field(self, path, bad_loc, read, expected):
        result, errors = load_json_with_default(_doc(**{path: 'bad'}), Outer, guess_default=True)

        assert [e.loc for e in errors] == [bad_loc]
        assert errors[0].type is ErrorType.TYPE_MISMATCH
        # The failing field is guessed, the siblings keep their value
        assert read(result) == 0
        assert (result.settings.retries, result.inner['a'], result.attrs_settings.retries) == expected
        assert (result.settings.name, result.attrs_settings.name) == ('ok', 'ok')

    @pytest.mark.parametrize('path, bad_loc, read, expected', CASES)
    def test_with_error_detail_resolves_the_field(self, path, bad_loc, read, expected):
        obj, detail = load_json_with_error_detail(_doc(**{path: 'bad'}), Outer)

        assert obj is NODEFAULT
        assert detail.loc == bad_loc
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('path, bad_loc, read, expected', CASES)
    def test_without_guessing_drops_the_entry(self, path, bad_loc, read, expected):
        """The failing field is required, so the entry holding it can not be repaired."""
        result, errors = load_json_with_default(_doc(**{path: 'bad'}), Outer)

        assert [e.loc for e in errors] == [bad_loc]
        assert result is NODEFAULT


class TestObjectKindAtRoot:
    """The model itself may be a plain kind, the path starts at its own field."""

    def test_dataclass(self):
        result, errors = load_json_with_default(
            b'{"name": "n", "retries": "bad"}', Settings, guess_default=True)

        assert result == Settings(name='n', retries=0)
        assert [e.loc for e in errors] == [('retries',)]

    def test_typeddict(self):
        result, errors = load_json_with_default(b'{"a": "bad"}', Inner, guess_default=True)

        assert result == {'a': 0}
        assert [e.loc for e in errors] == [('a',)]

    def test_attrs(self):
        result, errors = load_json_with_default(
            b'{"name": "n", "retries": "bad"}', AttrsSettings, guess_default=True)

        assert result == AttrsSettings(name='n', retries=0)
        assert [e.loc for e in errors] == [('retries',)]


# --- Array kinds: the path segment is a position ---


class TestArrayKinds:
    """A NamedTuple is decoded from an array, so an error gives its position."""

    def test_at_root(self):
        result, errors = load_json_with_default(b'[1, 2]', Pair, guess_default=True)

        assert result == Pair(a=1, b='')
        assert [e.loc for e in errors] == [(1,)]

    def test_at_root_with_error_detail(self):
        obj, detail = load_json_with_error_detail(b'[1, 2]', Pair)

        assert obj is NODEFAULT
        assert detail.loc == (1,)
        assert detail.loc_resolved is True
        assert detail.error_value == 2
        assert detail.error_value_resolved is True

    def test_as_a_struct_field(self):
        result, errors = load_json_with_default(b'{"pair": [1, 2], "n": 1}', NTBox, guess_default=True)

        assert result == NTBox(pair=Pair(a=1, b=''), n=1)
        assert [e.loc for e in errors] == [('pair', 1)]

    def test_as_a_struct_field_with_error_detail(self):
        obj, detail = load_json_with_error_detail(b'{"pair": [1, 2], "n": 1}', NTBox)

        assert detail.loc == ('pair', 1)
        assert detail.error_value == 2


# --- Union member: the path is relative to the member, not the union ---


class TestUnionMember:
    """The segment kind tells which union member the path belongs to."""

    def test_an_object_member(self):
        result, errors = load_json_with_default(
            b'{"kind": "a", "payload": {"name": "n", "retries": "bad"}}', Choice, guess_default=True)

        assert result == Choice(kind='a', payload=Settings(name='n', retries=0))
        assert [e.loc for e in errors] == [('payload', 'retries')]

    def test_an_object_member_with_error_detail(self):
        obj, detail = load_json_with_error_detail(
            b'{"kind": "a", "payload": {"name": "n", "retries": "bad"}}', Choice)

        assert detail.loc == ('payload', 'retries')
        assert detail.error_value == 'bad'

    def test_an_array_member(self):
        result, errors = load_json_with_default(
            b'{"kind": "a", "payload": [1, "bad"]}', Choice, guess_default=True)

        assert result == Choice(kind='a', payload=[1, 0])
        assert [e.loc for e in errors] == [('payload', 1)]

    def test_an_array_member_with_error_detail(self):
        obj, detail = load_json_with_error_detail(b'{"kind": "a", "payload": [1, "bad"]}', Choice)

        assert detail.loc == ('payload', 1)
        assert detail.error_value == 'bad'


# --- msgpack: the same model kinds through the other codec ---


class TestMsgpackObjectKinds:
    """msgpack reports the same locations, so the same walk repairs them."""

    @pytest.mark.parametrize('path, bad_loc, read, expected', TestObjectKinds.CASES)
    def test_with_default_repairs_only_the_bad_field(self, path, bad_loc, read, expected):
        result, errors = load_msgpack_with_default(
            msgspec.msgpack.encode(_obj(**{path: 'bad'})), Outer, guess_default=True)

        assert [e.loc for e in errors] == [bad_loc]
        assert read(result) == 0
        assert (result.settings.retries, result.inner['a'], result.attrs_settings.retries) == expected

    @pytest.mark.parametrize('path, bad_loc, read, expected', TestObjectKinds.CASES)
    def test_with_error_detail_resolves_the_field(self, path, bad_loc, read, expected):
        obj, detail = load_msgpack_with_error_detail(
            msgspec.msgpack.encode(_obj(**{path: 'bad'})), Outer)

        assert detail.loc == bad_loc
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'

    def test_a_namedtuple_field(self):
        data = msgspec.msgpack.encode({'pair': [1, 2], 'n': 1})

        result, errors = load_msgpack_with_default(data, NTBox, guess_default=True)

        assert result.pair == Pair(a=1, b='')
        assert [e.loc for e in errors] == [('pair', 1)]


# --- The same kinds through `convert`, which has no payload ---


class TestConvertObjectKinds:
    """An in-memory value is repaired with the same walk, in place."""

    def test_dataclass(self):
        data = _obj(**{'settings.retries': 'bad'})

        result, errors = convert_with_default(data, Outer, guess_default=True)

        assert result.settings.retries == 0
        assert [e.loc for e in errors] == [('settings', 'retries')]
        assert data['settings']['retries'] == 0

    def test_namedtuple(self):
        data = {'pair': [1, 2], 'n': 1}

        result, errors = convert_with_default(data, NTBox, guess_default=True)

        assert result.pair == Pair(a=1, b='')
        assert [e.loc for e in errors] == [('pair', 1)]
        assert data['pair'] == [1, '']


# --- The kind of a model is looked up once per path segment ---


class TestKindLookupCost:
    """`known_struct_type` keeps the walk from naming the same kind twice.

    Every segment of a path names one model, so one lookup per segment is the
    floor. Without the switch each field access names it again, which makes the
    walk pay for the same answer two or three times. A `...` placeholder names
    no model at all: it finds its key by validating the entries.
    """

    @staticmethod
    def _count(monkeypatch, modules):
        calls = []
        for module in modules:
            original = module.is_struct_like

            def counting(model, unwrap=True, _original=original):
                calls.append(model)
                return _original(model, unwrap=unwrap)

            monkeypatch.setattr(module, 'is_struct_like', counting)
        return calls

    def test_the_repair_walk(self, monkeypatch):
        import msgspecerror.parse_model
        import msgspecerror.repair

        calls = self._count(monkeypatch, [msgspecerror.repair, msgspecerror.parse_model])

        result, errors = load_json_with_default(
            b'{"settings": {"name": "n", "retries": "bad"}, "inner": {"a": 1}, '
            b'"attrs_settings": {"name": "n", "retries": 1}}', Outer, guess_default=True)

        assert [e.loc for e in errors] == [('settings', 'retries')]
        # One per segment: the struct, then the dataclass the failing field is in
        assert len(calls) == 2

    def test_the_resolve_walk(self, monkeypatch):
        import msgspecerror.parse_model
        import msgspecerror.with_error

        calls = self._count(monkeypatch, [msgspecerror.with_error, msgspecerror.parse_model])

        obj, detail = load_json_with_error_detail(
            b'{"settings": {"name": "n", "retries": "bad"}, "inner": {"a": 1}, '
            b'"attrs_settings": {"name": "n", "retries": 1}}', Outer)

        assert detail.loc == ('settings', 'retries')
        assert len(calls) == 2

    def test_a_placeholder_costs_no_lookup(self, monkeypatch):
        import msgspecerror.parse_model
        import msgspecerror.repair

        calls = self._count(monkeypatch, [msgspecerror.repair, msgspecerror.parse_model])

        result, errors = load_json_with_default(
            b'{"a": {"b": {"c": "bad"}}, "x": {"y": {"c": 1}}}',
            Dict[str, Dict[str, Dict[str, int]]], guess_default=True)

        assert [e.loc for e in errors] == [('a', 'b', 'c')]
        # Three placeholders, and not one of them needs a kind
        assert calls == []

    def test_a_placeholder_does_not_name_the_model_it_holds(self, monkeypatch):
        """The walk steps through a placeholder without asking what is inside."""
        import msgspecerror.parse_model
        import msgspecerror.repair

        calls = self._count(monkeypatch, [msgspecerror.repair, msgspecerror.parse_model])

        result, errors = load_json_with_default(
            b'{"rows": {"r1": {"settings": {"name": "n", "retries": "bad"}, "inner": {"a": 1}, '
            b'"attrs_settings": {"name": "n", "retries": 1}}}}', Dict[str, Dict[str, Outer]], guess_default=True)

        assert [e.loc for e in errors] == [('rows', 'r1', 'settings', 'retries')]
        # Both dict levels report a placeholder, so only the two models behind
        # them are named: the struct and the dataclass holding the failing field
        assert len(calls) == 2

    def test_a_union_member_is_named_once(self, monkeypatch):
        import msgspecerror.parse_model
        import msgspecerror.repair

        calls = self._count(monkeypatch, [msgspecerror.repair, msgspecerror.parse_model])

        result, errors = load_json_with_default(
            b'{"kind": "a", "payload": {"name": "n", "retries": "bad"}}', Choice, guess_default=True)

        assert [e.loc for e in errors] == [('payload', 'retries')]
        # The struct, then the two members the union has to tell apart. The kind
        # of the member that matched comes back with it, so it is not named again
        assert len(calls) == 3
