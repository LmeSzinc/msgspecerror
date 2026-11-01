"""Tests for `convert_with_default`, the in-memory entry of the `_with_default` family.

The repair engine itself is covered by the other modules under `tests/repair/`.
What matters here is the boundary of the new entry point:

1. it agrees with `load_json_with_default` on the same payload;
2. it repairs the caller's object in place, which `load_*` does to its own
   freshly decoded object;
3. it must not touch the input when the repair fails.
"""
import copy
from typing import Dict, List, Tuple

import msgspec
import pytest
from msgspec import NODEFAULT, Struct, ValidationError, convert

from msgspecerror import convert_with_default
from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default

# --- Test Models ---


class Req(Struct):
    """`a` has no default, `b` does, so a minimal repair of `a` fails."""
    a: int
    b: str = ''


class Item(Struct):
    amount: int = 0


class Inventory(Struct):
    items: Dict[str, Item] = {}


class Pair(Struct):
    name: str = 'x'
    values: Tuple[int, str] = (0, '')


class Cell(Struct):
    v: int = 0


class StrictModel(Struct, forbid_unknown_fields=True):
    """A stray key can not be repaired, the repair gives up after the fixes before it."""
    cells: Dict[str, Cell] = {}


class StrictDict(Struct, forbid_unknown_fields=True):
    values: Dict[str, int] = {}


class StrictList(Struct, forbid_unknown_fields=True):
    values: List[int] = []


class StrictInventory(Struct, forbid_unknown_fields=True):
    items: Dict[str, Item] = {}


# `(model, data)` pairs that fail validation. The same data goes through both
# `convert_with_default` and `load_json_with_default`.
FAILING_CASES = [
    ('required-field', Req, {'a': 'bad'}),
    ('unknown-field-dict', Dict[str, int], {'k': 'x'}),
    ('dict-of-struct', Inventory, {'items': {'apple': {'amount': 'ABC'}}}),
    ('nested-dict', Dict[str, Dict[str, int]], {'a': {'b': 'x'}}),
    ('list-item', List[int], [1, 'x']),
    ('tuple-item', Tuple[int, str], [1, 2]),
    ('fixed-tuple-field', Pair, {'values': [1, 2]}),
]

ROOT_CASES = [
    ('str-to-int', int, 'abc'),
    ('list-to-dict', Dict[str, int], ['x']),
]


def assert_same_as_loader(model, data, guess_default):
    """`convert_with_default` and `load_json_with_default` must agree."""
    result, errors = convert_with_default(
        copy.deepcopy(data), model, guess_default=guess_default)
    expected_result, expected_errors = load_json_with_default(
        msgspec.json.encode(data), model, guess_default=guess_default)

    assert (result is NODEFAULT) == (expected_result is NODEFAULT)
    if result is not NODEFAULT:
        assert result == expected_result
    assert [e.loc for e in errors] == [e.loc for e in expected_errors]
    assert [e.type for e in errors] == [e.type for e in expected_errors]


class TestSuccess:
    def test_returns_the_model_and_no_error(self):
        result, errors = convert_with_default({'a': 1}, Req)
        assert result == Req(a=1)
        assert errors == []

    def test_does_not_modify_the_input(self):
        data = {'a': 1, 'b': 'x'}
        convert_with_default(data, Req)
        assert data == {'a': 1, 'b': 'x'}

    def test_guess_default_is_irrelevant_on_success(self):
        result, errors = convert_with_default({'a': 1}, Req, guess_default=True)
        assert result == Req(a=1)
        assert errors == []

    def test_decoder_is_accepted(self):
        result, errors = convert_with_default({'a': 1}, msgspec.json.Decoder(Req))
        assert result == Req(a=1)
        assert errors == []

    def test_msgpack_decoder_is_accepted(self):
        result, errors = convert_with_default({'a': 1}, msgspec.msgpack.Decoder(Req))
        assert result == Req(a=1)
        assert errors == []


class TestRepairInPlace:
    """A successful repair writes into the object the caller passed in."""

    def test_field_default_is_written_into_the_input(self):
        data = {'items': {'apple': {'amount': 'ABC'}}}
        result, errors = convert_with_default(data, Inventory)

        assert result == Inventory(items={'apple': Item(amount=0)})
        assert [e.loc for e in errors] == [('items', 'apple', 'amount')]
        # The caller's dict is the one that got repaired
        assert data == {'items': {'apple': {'amount': 0}}}

    def test_guessed_default_is_written_into_the_input(self):
        data = {'a': 'bad'}
        result, errors = convert_with_default(data, Req, guess_default=True)

        assert result == Req(a=0, b='')
        assert [e.loc for e in errors] == [('a',)]
        assert data == {'a': 0}

    def test_dict_entry_is_deleted_from_the_input(self):
        data = {'k': 'x', 'ok': 1}
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {'ok': 1}
        assert [e.loc for e in errors] == [('k',)]
        assert data == {'ok': 1}

    def test_list_item_is_deleted_from_the_input(self):
        data = [1, 'x']
        result, errors = convert_with_default(data, List[int])

        assert result == [1]
        assert [e.loc for e in errors] == [(1,)]
        assert data == [1]

    def test_nested_object_identity_is_kept(self):
        """The repair mutates the caller's nested object, it does not copy it."""
        inner = {'amount': 'ABC'}
        data = {'items': {'apple': inner}}
        convert_with_default(data, Inventory)

        assert data['items']['apple'] is inner
        assert inner == {'amount': 0}


class TestFailedRepair:
    """A repair that finds no default leaves the input alone."""
    def test_required_field_without_default(self):
        data = {'a': 'bad'}
        result, errors = convert_with_default(data, Req)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [('a',)]
        assert data == {'a': 'bad'}

    def test_root_error_does_not_modify_the_input(self):
        data = 'abc'
        result, errors = convert_with_default(data, int)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [()]
        assert data == 'abc'

    def test_root_container_error_does_not_modify_the_input(self):
        data = ['x']
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {}
        assert [e.loc for e in errors] == [()]
        assert data == ['x']


class TestFailedRepairPutsTheInputBack:
    """A repair that gives up after it has written something must undo those writes.

    The repair fixes one error per round. A later error that no default can
    settle makes it give up, and the fixes of the earlier rounds are already in
    the caller's object. Answering with a default object while leaving the
    caller's object half repaired is the one outcome that helps nobody.
    """

    def test_a_half_repaired_object_is_put_back(self):
        data = {'cells': {'c': {'v': 'bad'}}, 'z': 1}
        result, errors = convert_with_default(data, StrictModel)

        assert result == StrictModel(cells={})
        assert [e.loc for e in errors] == [('cells', 'c', 'v'), ('z',)]
        assert data == {'cells': {'c': {'v': 'bad'}}, 'z': 1}

    def test_a_nested_repaired_field_is_put_back(self):
        inner = {'amount': 'ABC'}
        data = {'items': {'apple': inner}, 'z': 0}
        result, errors = convert_with_default(data, StrictInventory)

        assert result == StrictInventory(items={})
        assert data['items']['apple'] is inner
        assert inner == {'amount': 'ABC'}

    def test_a_deleted_dict_entry_keeps_its_position(self):
        data = {'values': {'a': 1, 'bad': 'x', 'b': 2}, 'z': 0}
        result, errors = convert_with_default(data, StrictDict)

        assert result == StrictDict(values={})
        assert data == {'values': {'a': 1, 'bad': 'x', 'b': 2}, 'z': 0}
        assert list(data['values']) == ['a', 'bad', 'b']

    def test_a_deleted_list_item_is_put_back_at_its_index(self):
        data = {'values': [1, 'bad', 3], 'z': 0}
        result, errors = convert_with_default(data, StrictList)

        assert result == StrictList(values=[])
        assert data == {'values': [1, 'bad', 3], 'z': 0}

    def test_the_same_object_can_be_retried(self):
        """The restored object fails the same way, so a retry is a real retry."""
        data = {'cells': {'c': {'v': 'bad'}}, 'z': 1}
        convert_with_default(data, StrictModel)
        result, errors = convert_with_default(data, StrictModel)

        assert result == StrictModel(cells={})
        assert [e.loc for e in errors] == [('cells', 'c', 'v'), ('z',)]
        assert data == {'cells': {'c': {'v': 'bad'}}, 'z': 1}


class TestUndoJournal:
    """The journal behind `TestFailedRepairPutsTheInputBack`."""

    def test_a_container_is_copied_once(self):
        from msgspecerror.repair import _Undo

        data = {'a': 1}
        undo = _Undo()
        undo.touch(data)
        data['a'] = 2
        undo.touch(data)
        data['b'] = 3

        undo.restore()

        assert data == {'a': 1}

    def test_a_list_is_restored_in_place(self):
        from msgspecerror.repair import _Undo

        data = [1, 2, 3]
        undo = _Undo()
        undo.touch(data)
        del data[1]

        undo.restore()

        assert data == [1, 2, 3]

    def test_restore_does_not_keep_the_snapshot(self):
        from msgspecerror.repair import _Undo

        data = {'a': 1}
        undo = _Undo()
        undo.touch(data)
        data['a'] = 2
        undo.restore()
        data['a'] = 3
        undo.restore()

        assert data == {'a': 3}


class TestMatchesLoadJsonWithDefault:
    """The two entries of the `_with_default` family share one repair engine."""

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('name, model, data', FAILING_CASES)
    def test_failing_cases(self, name, model, data, guess_default):
        assert_same_as_loader(model, data, guess_default)

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('name, model, data', ROOT_CASES)
    def test_root_cases(self, name, model, data, guess_default):
        assert_same_as_loader(model, data, guess_default)

    @pytest.mark.parametrize('name, model, data', [
        ('ok', Req, {'a': 1}),
        ('ok-dict', Dict[str, int], {'k': 1}),
    ])
    def test_success_cases(self, name, model, data):
        assert_same_as_loader(model, data, False)


class TestErrorDetails:
    def test_error_type_is_preserved(self):
        _, errors = convert_with_default({'k': 'x'}, Dict[str, int])
        assert errors[0].type is ErrorType.TYPE_MISMATCH
        assert errors[0].ctx.expected == 'int'
        assert errors[0].ctx.got == 'str'

    def test_constraint_error_is_preserved(self):
        from typing_extensions import Annotated

        model = Dict[str, Annotated[int, msgspec.Meta(ge=18)]]
        result, errors = convert_with_default({'k': 10}, model)

        assert result is NODEFAULT or result == {}
        assert errors[0].type is ErrorType.NUMERIC_CONSTRAINT
        assert errors[0].ctx.ge == 18

    def test_multiple_errors_are_all_reported(self):
        data = {'a': 'x', 'b': 'y'}
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {}
        assert [e.loc for e in errors] == [('a',), ('b',)]
        assert data == {}

    def test_invalid_type_hint_raises(self):
        """A wrong argument is a caller bug, not bad data."""
        with pytest.raises(TypeError):
            convert_with_default({}, 12345)

    def test_valid_data_is_not_reported_as_an_error(self):
        result, errors = convert_with_default({'a': 1}, Req)
        assert errors == []
        # The result is accepted by the model it claims to be
        assert convert(result, Req) == result

    def test_failed_result_is_never_a_partial_model(self):
        """A failed repair returns NODEFAULT, not a mix of repaired and raw data."""
        data = {'items': {'apple': {'amount': 'ABC'}}}
        result, _ = convert_with_default(data, Req)
        assert result is NODEFAULT
        with pytest.raises(ValidationError):
            convert(data, Req)


class Obj:
    """A plain object, the shape `from_attributes=True` is for."""

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


class TestFromAttributes:
    """`from_attributes=True`: repair a copy of the attributes, never the object.

    The attributes are read into a dict first, because the repair engine writes
    into a dict and the caller's object must not be changed. Before that, the
    object was never read at all: the field branch only accepts a dict, so the
    repair gave up and answered with a root default, losing the fields that had
    passed.
    """

    def test_a_good_field_is_kept(self):
        result, errors = convert_with_default(
            Obj(a='bad', b='ok'), Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]

    def test_the_object_is_not_modified(self):
        source = Obj(a='bad', b='ok')

        convert_with_default(source, Req, from_attributes=True, guess_default=True)

        assert (source.a, source.b) == ('bad', 'ok')

    def test_a_required_field_no_longer_fails_the_whole_repair(self):
        """`b` has no default, so an unreadable object used to answer NODEFAULT."""

        class Both(Struct):
            a: int
            b: str

        result, errors = convert_with_default(
            Obj(a='bad', b='ok'), Both, from_attributes=True, guess_default=True)

        assert result == Both(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]

    def test_a_struct_as_the_source(self):
        class Source(Struct):
            a: int = 0
            b: str = 'x'

        source = Source(a='bad', b='ok')
        result, errors = convert_with_default(source, Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]
        assert source == Source(a='bad', b='ok')

    def test_a_dataclass_as_the_source(self):
        import dataclasses

        @dataclasses.dataclass
        class Source:
            a: int = 0
            b: str = 'x'

        result, errors = convert_with_default(
            Source(a='bad', b='ok'), Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]

    def test_an_attrs_class_as_the_source(self):
        attrs = pytest.importorskip('attrs')

        @attrs.define
        class Source:
            a: int = 0
            b: str = 'x'

        result, errors = convert_with_default(
            Source(a='bad', b='ok'), Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]

    def test_a_nested_object_is_repaired_too(self):
        class Inner(Struct):
            n: int = 0

        class Outer(Struct):
            inner: Inner = None
            tag: str = 't'

        source = Obj(inner=Obj(n='bad'), tag='keep')

        result, errors = convert_with_default(
            source, Outer, from_attributes=True, guess_default=True)

        assert result == Outer(inner=Inner(n=0), tag='keep')
        assert [e.loc for e in errors] == [('inner', 'n')]
        assert source.inner.n == 'bad'

    def test_a_container_field_is_copied(self):
        """A repair deletes from the dict it walks, which must not be the caller's."""
        source = Obj(items={'ok': 1, 'bad': 'x'}, tag='keep')

        result, errors = convert_with_default(
            source, Dict2, from_attributes=True)

        assert result == Dict2(items={'ok': 1}, tag='keep')
        assert source.items == {'ok': 1, 'bad': 'x'}

    def test_a_dict_subclass_attribute_is_copied(self):
        """The engine writes into `isinstance(dict)`, so the copy has to as well.

        An exact `type(value) is dict` test let an `OrderedDict` attribute through
        by reference, and repairing the snapshot deleted a key out of the
        caller's own dict.
        """
        from collections import OrderedDict

        class Holder:
            def __init__(self):
                self.items = OrderedDict([('ok', 1), ('bad', 'x')])
                self.tag = 'keep'

        holder = Holder()
        result, errors = convert_with_default(holder, Dict2, from_attributes=True)

        assert result == Dict2(items={'ok': 1}, tag='keep')
        assert [e.loc for e in errors] == [('items', 'bad')]
        assert holder.items == OrderedDict([('ok', 1), ('bad', 'x')])

    def test_a_list_subclass_attribute_is_copied(self):
        class MyList(list):
            pass

        class Holder:
            def __init__(self):
                self.xs = MyList([1, 'x'])
                self.tag = 'keep'

        holder = Holder()
        result, errors = convert_with_default(holder, ListModel, from_attributes=True)

        assert result == ListModel(xs=[1], tag='keep')
        assert [e.loc for e in errors] == [('xs', 1)]
        assert holder.xs == MyList([1, 'x'])

    def test_a_read_only_property_is_read(self):
        class WithProperty:
            def __init__(self):
                self.a = 'bad'

            @property
            def b(self):
                return 'computed'

        result, errors = convert_with_default(
            WithProperty(), Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='computed')

    def test_an_unknown_attribute_does_not_leak_into_the_model(self):
        result, _ = convert_with_default(
            Obj(a=1, b='ok', extra='ignored'), Req, from_attributes=True)

        assert result == Req(a=1, b='ok')

    def test_an_object_that_holds_itself_terminates(self):
        class Loop(Struct):
            a: int = 0
            me: object = None

        source = Obj(a='bad')
        source.me = source

        result, errors = convert_with_default(
            source, Loop, from_attributes=True, guess_default=True)

        assert result.a == 0
        assert [e.loc for e in errors] == [('a',)]

    @pytest.mark.parametrize('data', [5, 'text', [1, 2], None])
    def test_data_without_attributes_keeps_the_old_answer(self, data):
        """A scalar can not be read by attribute, it is a root error as before."""
        result, errors = convert_with_default(data, Req, from_attributes=True, guess_default=True)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [()]

    def test_the_option_off_keeps_the_old_answer(self):
        """Without the option an object is not a mapping, and that is a root error."""
        result, errors = convert_with_default(Obj(a='bad', b='ok'), Req, guess_default=True)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [()]

    def test_a_dataclass_pseudo_field_is_not_read(self):
        """`__dataclass_fields__` holds ClassVar / InitVar, msgspec decodes neither.

        Reading them would put a key into the snapshot that no field maps to.
        """
        import dataclasses
        import typing

        @dataclasses.dataclass
        class Source:
            a: int = 0
            b: str = 'x'
            cv: typing.ClassVar[int] = 5

        from msgspecerror.locate import _attribute_snapshot

        source = Source(a='bad', b='ok')
        source.cv = 9

        assert _attribute_snapshot(source, Source, 'dataclass') == {'a': 'bad', 'b': 'ok'}

        result, errors = convert_with_default(
            source, Source2, from_attributes=True, guess_default=True)

        assert result == Source2(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]

    @pytest.mark.parametrize('data', [
        [1, 2], (1,), {1, 2}, 5, 1.5, 'text', b'ab', None,
        # a mapping subclass with an attribute of its own: msgspec reads it by
        # key, so the attribute must not be taken as the field
        'mapping-subclass', 'list-subclass',
    ])
    def test_a_builtin_value_is_answered_before_the_field_names(self, data, monkeypatch):
        """No attribute to read, so the field names are never even listed."""
        import msgspecerror.locate as locate_mod

        if data == 'mapping-subclass':
            from collections import OrderedDict

            class MappingWithAttributes(OrderedDict):
                pass

            value = MappingWithAttributes({'b': 'ok'})
            value.a = 5
        elif data == 'list-subclass':
            class ListSubclass(list):
                pass

            value = ListSubclass([1])
        else:
            value = data

        looked_up = []
        monkeypatch.setattr(
            locate_mod, '_snapshot_field_names',
            lambda *args, **kwargs: looked_up.append(args) or ())

        assert locate_mod._attribute_snapshot(value, Req, 'struct') is None
        assert looked_up == []

    @pytest.mark.parametrize('kind', ['typeddict', 'namedtuple', '', None])
    def test_a_model_that_is_not_an_object_never_reads_an_attribute(self, kind, monkeypatch):
        """A TypedDict is a dict and a NamedTuple is an array: neither has fields.

        `Explodes` raises on any attribute read, so reaching for one fails the
        test instead of being silently skipped by an `AttributeError`.
        """

        class Explodes:
            def __getattr__(self, name):
                raise AssertionError(f'attribute {name!r} must not be read')

        import msgspecerror.locate as locate_mod

        looked_up = []
        monkeypatch.setattr(
            locate_mod, '_snapshot_field_names',
            lambda *args, **kwargs: looked_up.append(args) or ())

        assert locate_mod._attribute_snapshot(Explodes(), Req, kind) is None
        assert looked_up == []

    def test_a_model_with_no_readable_field_is_refused(self):
        """An object of another shape is not a source, and the caller keeps its
        old answer instead of repairing an empty dict."""
        from msgspecerror.locate import _attribute_snapshot

        assert _attribute_snapshot(5, Req, 'struct') is None
        assert _attribute_snapshot({}, Req, 'struct') is None

    def test_a_typeddict_model_keeps_the_dict_behaviour(self):
        from typing_extensions import TypedDict

        class TD(TypedDict):
            a: int

        result, errors = convert_with_default({'a': 'bad'}, TD, from_attributes=True, guess_default=True)

        assert result == {'a': 0}
        assert [e.loc for e in errors] == [('a',)]

    def test_a_mapping_is_answered_by_the_snapshot_itself(self):
        """A dict has no attributes, and the snapshot says so on its own.

        The caller repeats no condition: `_attribute_snapshot()` is the one
        place that decides, so the two can not disagree.
        """
        from msgspecerror.locate import _attribute_snapshot

        assert _attribute_snapshot({'a': 'bad'}, Req, 'struct') is None

        data = {'a': 'bad', 'b': 'ok'}
        result, errors = convert_with_default(data, Req, from_attributes=True, guess_default=True)

        assert result == Req(a=0, b='ok')
        assert [e.loc for e in errors] == [('a',)]
        # A dict is still repaired in place, the snapshot does not take it over
        assert data == {'a': 0, 'b': 'ok'}

    def test_a_renamed_model_keeps_the_old_answer(self):
        """A dict is decoded by the encoded name, the error names the attribute.

        Mixing the two would silently drop every renamed field, so this shape is
        left to the old behaviour instead of being repaired wrongly. See P2-17.
        """

        class Renamed(Struct, rename='camel'):
            some_field: int = 0
            other: str = 'y'

        result, errors = convert_with_default(
            Obj(some_field='bad', other='keep'), Renamed, from_attributes=True, guess_default=True)

        assert result == Renamed(some_field=0, other='y')
        assert [e.loc for e in errors] == [('some_field',)]


class Dict2(Struct):
    """A field that holds a dict, for the copy test."""
    items: Dict[str, int] = {}
    tag: str = 't'


class Source2(Struct):
    """Two fields, so a snapshot that drops one is visible."""
    a: int = 0
    b: str = 'x'


class ListModel(Struct):
    """A field that holds a list, for the subclass copy test."""
    xs: List[int] = []
    tag: str = 't'


class TestDecoderOptions:
    """The repair does not fight the rules the caller validated with."""

    DATA = {"items": {"good": {"amount": "1"}, "bad": {"amount": "xx"}}}

    def test_strict_false_option(self):
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), Inventory, guess_default=True, strict=False)

        assert result == Inventory(items={"good": Item(amount=1), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [("items", "bad", "amount")]

    def test_without_the_option_both_entries_fail(self):
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), Inventory, guess_default=True)

        assert result == Inventory(items={"good": Item(amount=0), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [
            ("items", "good", "amount"), ("items", "bad", "amount")]

    def test_decoder_options_are_used(self):
        decoder = msgspec.json.Decoder(Inventory, strict=False)
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), decoder, guess_default=True)

        assert result == Inventory(items={"good": Item(amount=1), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [("items", "bad", "amount")]

    def test_explicit_option_overrides_the_decoder(self):
        decoder = msgspec.json.Decoder(Inventory, strict=False)
        result, errors = convert_with_default(
            copy.deepcopy(self.DATA), decoder, guess_default=True, strict=True)

        assert result == Inventory(items={"good": Item(amount=0), "bad": Item(amount=0)})
        assert [e.loc for e in errors] == [
            ("items", "good", "amount"), ("items", "bad", "amount")]

    def test_dec_hook_option(self):
        class Point:
            def __init__(self, value):
                self.value = value

        def dec_hook(typ, obj):
            if typ is Point:
                return Point(obj)
            raise NotImplementedError

        class Layer(Struct):
            p: Point
            n: int = 0

        class Doc(Struct):
            layers: Dict[str, Layer] = {}

        data = {"layers": {"one": {"p": [1, 2], "n": 1}, "bad": {"p": [3, 4], "n": "xx"}}}
        result, errors = convert_with_default(data, Doc, guess_default=True, dec_hook=dec_hook)

        assert len(result.layers) == 2
        assert result.layers["one"].p.value == [1, 2]
        assert result.layers["bad"].n == 0
        assert [e.loc for e in errors] == [("layers", "bad", "n")]


class TestRawFieldModel:
    """An in-memory object has no bytes for a `msgspec.Raw` field.

    The value is encoded as msgpack to fill the field, so the model can be built
    and the field still holds the sub-document: `msgpack.decode(payload)` gives
    back what the caller passed. A real error behind the field is found and
    repaired like any other, and the caller's sub-document is never replaced by
    a placeholder `Raw`.
    """

    class Msg(Struct):
        payload: msgspec.Raw
        n: int = 0

    def test_the_model_is_built(self):
        class Doc(Struct):
            items: Dict[str, self.Msg] = {}

        data = {'items': {'one': {'payload': {'a': 1}, 'n': 1}}}
        result, errors = convert_with_default(data, Doc)

        assert errors == []
        assert msgspec.msgpack.decode(result.items['one'].payload) == {'a': 1}

    def test_a_real_error_behind_the_raw_field_is_repaired(self):
        class Doc(Struct):
            items: Dict[str, self.Msg] = {}

        data = {'items': {'one': {'payload': {'a': 1}, 'n': 'bad'}}}
        result, errors = convert_with_default(data, Doc, guess_default=True)

        assert result.items['one'].n == 0
        assert msgspec.msgpack.decode(result.items['one'].payload) == {'a': 1}
        assert [e.loc for e in errors] == [('items', 'one', 'n')]

    def test_the_raw_field_keeps_the_sub_document(self):
        """The old behaviour wrote `Raw(b'')` over the original sub-document.

        A raw field with a default is the case that used to corrupt the input:
        the repair wrote the default value into the caller's object. The field
        now keeps what the caller passed, whatever the msgspec version does
        with the rest of the object.
        """

        class Defaulted(Struct):
            payload: msgspec.Raw = msgspec.Raw(b'')
            n: int = 0

        data = {'payload': {'a': 1}, 'n': 'bad'}
        convert_with_default(data, Defaulted, guess_default=True)

        assert data['payload'] == {'a': 1}

    def test_an_uncarryable_value_is_reported_not_crashed(self):
        """msgpack can not encode every object, so the field can not be filled."""

        class Opaque:
            pass

        data = {'payload': Opaque(), 'n': 1}
        result, errors = convert_with_default(data, self.Msg)

        assert result is NODEFAULT
        assert [e.type for e in errors] == [ErrorType.INPUT_REJECTED]

    def test_a_value_that_refers_to_itself_is_reported_not_crashed(self):
        """A YAML anchor gives a cycle, which the encoder can not walk either.

        `msgpack.encode()` raises `RecursionError` for it, not `EncodeError`, so
        the field could not be filled and the error escaped the repair.
        """
        cyclic = {'a': None}
        cyclic['a'] = cyclic

        result, errors = convert_with_default(cyclic, msgspec.Raw)

        assert result is NODEFAULT
        assert [e.type for e in errors] == [ErrorType.INPUT_REJECTED]

    def test_a_cycle_inside_a_raw_field_is_reported_not_crashed(self):
        inner = {'a': 1}
        inner['self'] = inner
        data = {'payload': inner, 'n': 'bad'}

        result, errors = convert_with_default(data, self.Msg, guess_default=True)

        assert result is NODEFAULT
        assert [e.type for e in errors] == [ErrorType.INPUT_REJECTED]
