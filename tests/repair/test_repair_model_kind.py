"""Repair inside every model kind msgspec decodes into, not only ``msgspec.Struct``.

An error path names a field for a model decoded from an object (dataclass,
TypedDict, attrs) and gives a position for one decoded from an array
(NamedTuple, ``array_like=True`` struct). The walk reads the field list of
whichever kind it is holding, so a repair inside one of them lands on the field
that failed instead of the whole document.
"""
import copy
import dataclasses
from typing import Dict, Generic, List, NamedTuple, Optional, TypeVar, Union

import attrs
import msgspec.json
import msgspec.msgpack
import pytest
from msgspec import NODEFAULT, Struct, field
from typing_extensions import NotRequired, TypedDict

from msgspecerror import (
    ErrorInfo, convert_with_default, load_json_with_default,
    load_json_with_error_detail, load_msgpack_with_default, load_msgpack_with_error_detail,
    resolve_error
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


class ArrayInner(Struct):
    """The field an array position addresses, so the path goes one level deeper."""
    a: int = 0


class ArrayOuter(Struct, array_like=True):
    """An `array_like` struct whose first field is itself a model."""
    inner: ArrayInner = field(default_factory=ArrayInner)
    x: int = 0


class ArrayOuterOfOuter(Struct, array_like=True):
    """Two array levels, so the walk has to descend through both."""
    pair: ArrayOuter = field(default_factory=ArrayOuter)
    y: int = 0


class NTNested(NamedTuple):
    """A NamedTuple whose first field is itself a model."""
    inner: ArrayInner
    x: int = 0


class ArrayInnerRequired(Struct):
    """No default, so a repair inside it has nothing to write."""
    a: int


class ArrayOuterRequired(Struct, array_like=True):
    """An `array_like` struct holding a model nothing can be written into."""
    inner: ArrayInnerRequired
    x: int = 0


class TaggedArrayOuter(Struct, tag=True, array_like=True):
    """The tag holds position 0, so a field is one position past it."""
    inner: ArrayInner = field(default_factory=ArrayInner)
    x: int = 0


class ArrayInnerFive(Struct):
    """A field with a default nothing else has, so a wrong slot is visible."""
    a: int = 5


class TaggedBase(Struct, tag=True, array_like=True):
    """A tagged array model to inherit from."""
    base: ArrayInner = field(default_factory=ArrayInner)


class TaggedChild(TaggedBase):
    """Inherited fields keep their order, the tag stays at position 0."""
    extra: ArrayInnerFive = field(default_factory=ArrayInnerFive)


class TaggedOfUntagged(Struct, tag=True, array_like=True):
    """A tagged array holding an untagged one: only the outer has an offset."""
    untagged: ArrayOuter = field(default_factory=ArrayOuter)
    y: int = 0


class UntaggedOfTagged(Struct, array_like=True):
    """An untagged array holding a tagged one: only the inner has an offset."""
    tagged: TaggedArrayOuter = field(default_factory=TaggedArrayOuter)
    y: int = 0


class Choice(Struct):
    """A union whose members are an object kind and an array kind."""
    kind: str
    payload: Union[Settings, List[int]]


T = TypeVar('T')


class GenericLeaf(Struct):
    """The type a generic array position is parametrized with."""
    a: int = 0


class GenericBox(Struct, Generic[T], array_like=True):
    """A generic `array_like` struct: position 0 is `T`, not a declared type."""
    x: T


class GenericHolder(Struct):
    """A generic array model nested in a plain struct."""
    box: GenericBox[GenericLeaf]
    n: int = 0


class GenericObject(Struct, Generic[T]):
    """A generic struct decoded from an object, which is addressed by name."""
    x: T


class GenericObjectHolder(Struct):
    """A generic object model nested in a plain struct."""
    box: GenericObject[GenericLeaf]


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


# --- An error inside the field a position addresses ---


class TestArrayKindDeeperThanThePosition:
    """The error is inside the field the position names, not at the position.

    The position names a field of the array-decoded model, the next segment names
    a field of *that* model. The walk has to descend into the value, the same
    way it descends through a list item: without it the whole document fell
    back to the root default although every field on the way has a default.
    """

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    def test_at_root(self, guess_default):
        result, errors = load_json_with_default(
            b'[{"a": "bad"}, 1]', ArrayOuter, guess_default=guess_default)

        assert result == ArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(0, 'a')]

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    def test_in_a_dict(self, guess_default):
        """A keyed entry is repaired in place, the valid sibling is kept."""
        data = b'{"k": [{"a": "bad"}, 1], "ok": [{"a": 2}, 3]}'

        result, errors = load_json_with_default(data, Dict[str, ArrayOuter], guess_default=guess_default)

        assert result == {
            'k': ArrayOuter(inner=ArrayInner(a=0), x=1),
            'ok': ArrayOuter(inner=ArrayInner(a=2), x=3),
        }
        assert [e.loc for e in errors] == [('k', 0, 'a')]

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    def test_in_a_list(self, guess_default):
        """An item that can be repaired is not dropped, its sibling stays."""
        data = b'[[{"a": "bad"}, 1], [{"a": 2}, 3]]'

        result, errors = load_json_with_default(data, List[ArrayOuter], guess_default=guess_default)

        assert result == [
            ArrayOuter(inner=ArrayInner(a=0), x=1),
            ArrayOuter(inner=ArrayInner(a=2), x=3),
        ]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    def test_a_namedtuple(self, guess_default):
        """A NamedTuple is addressed by position too, so it descends the same way."""
        result, errors = load_json_with_default(
            b'[{"a": "bad"}, 1]', NTNested, guess_default=guess_default)

        assert result == NTNested(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(0, 'a')]

    def test_two_array_levels(self):
        """Each position descends into the next model."""
        result, errors = load_json_with_default(b'[[{"a": "bad"}, 1], 2]', ArrayOuterOfOuter)

        assert result == ArrayOuterOfOuter(pair=ArrayOuter(inner=ArrayInner(a=0), x=1), y=2)
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_the_location_matches_the_detail(self):
        """The repair and the detail family agree on where the error is."""
        data = b'[[{"a": "bad"}, 1], [{"a": 2}, 3]]'
        obj, detail = load_json_with_error_detail(data, List[ArrayOuter])
        result, errors = load_json_with_default(data, List[ArrayOuter])

        assert obj is NODEFAULT
        assert detail.loc == errors[0].loc == (0, 0, 'a')
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True
        assert result == [
            ArrayOuter(inner=ArrayInner(a=0), x=1),
            ArrayOuter(inner=ArrayInner(a=2), x=3),
        ]

    def test_an_entry_with_nothing_to_write_is_dropped(self):
        """The inner field has no default, so the entry that holds the array goes.

        Only that entry: dropping the position itself would shift every later
        field of the array into the wrong place.
        """
        data = b'[[{"a": "bad"}, 1], [{"a": 2}, 3]]'

        result, errors = load_json_with_default(data, List[ArrayOuterRequired])

        assert result == [ArrayOuterRequired(inner=ArrayInnerRequired(a=2), x=3)]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_the_root_gives_up_when_nothing_can_be_written(self):
        """A root object has no container to drop it from."""
        result, errors = load_json_with_default(b'[{"a": "bad"}, 1]', ArrayOuterRequired)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [(0, 'a')]

    def test_a_guessed_value_fixes_an_entry_that_has_no_default(self):
        """`guess_default` supplies the field, so nothing has to be dropped."""
        data = b'[[{"a": "bad"}, 1], [{"a": 2}, 3]]'

        result, errors = load_json_with_default(data, List[ArrayOuterRequired], guess_default=True)

        assert result == [
            ArrayOuterRequired(inner=ArrayInnerRequired(a=0), x=1),
            ArrayOuterRequired(inner=ArrayInnerRequired(a=2), x=3),
        ]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_msgpack(self):
        """The msgpack family walks the same object the json family does."""
        payload = msgspec.msgpack.encode([[{'a': 'bad'}, 1], [{'a': 2}, 3]])

        result, errors = load_msgpack_with_default(payload, List[ArrayOuter])

        assert result == [
            ArrayOuter(inner=ArrayInner(a=0), x=1),
            ArrayOuter(inner=ArrayInner(a=2), x=3),
        ]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_convert(self):
        """An in-memory object walks the same way, and is written in place."""
        data = [{'a': 'bad'}, 1]

        result, errors = convert_with_default(data, ArrayOuter)

        assert result == ArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(0, 'a')]
        assert data == [{'a': 0}, 1]

    def test_a_valid_array_is_untouched(self):
        result, errors = load_json_with_default(b'[[{"a": 1}, 2]]', List[ArrayOuter])

        assert result == [ArrayOuter(inner=ArrayInner(a=1), x=2)]
        assert errors == []


class TestTaggedArrayKind:
    """A tagged `array_like` struct encodes the tag first, the fields after it.

    `msgspec` reports the position in that array, so `$[1]` names the first
    field. Reading position 1 as the second field wrote the wrong default into
    the slot: `inner` was filled with `x`'s value.
    """

    PAYLOAD = b'["TaggedArrayOuter", {"a": "bad"}, 1]'

    def test_a_field_one_level_deeper(self):
        result, errors = load_json_with_default(self.PAYLOAD, TaggedArrayOuter)

        assert result == TaggedArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(1, 'a')]

    def test_the_detail_agrees(self):
        obj, detail = load_json_with_error_detail(self.PAYLOAD, TaggedArrayOuter)

        assert obj is NODEFAULT
        assert detail.loc == (1, 'a')
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_the_whole_field(self):
        """Position 1 is the first field, so its own default fills the slot."""
        payload = b'["TaggedArrayOuter", "bad", 1]'

        result, errors = load_json_with_default(payload, TaggedArrayOuter)

        assert result == TaggedArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(1,)]

    def test_the_last_field_is_not_offset(self):
        """The position of the second field is 2, not 1."""
        payload = b'["TaggedArrayOuter", {"a": 1}, "bad"]'

        result, errors = load_json_with_default(payload, TaggedArrayOuter)

        assert result == TaggedArrayOuter(inner=ArrayInner(a=1), x=0)
        assert [e.loc for e in errors] == [(2,)]

    def test_the_tag_itself_is_not_a_field(self):
        """A wrong tag names no field, so it is not repaired as one."""
        payload = b'[1, {"a": 1}, 2]'

        result, errors = load_json_with_default(payload, TaggedArrayOuter)

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [(0,)]

    def test_an_untagged_array_kind_is_untouched(self):
        """The offset belongs to the tag, not to every array position."""
        result, errors = load_json_with_default(b'[{"a": "bad"}, 1]', ArrayOuter)

        assert result == ArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(0, 'a')]

    def test_in_a_list(self):
        """The offset is applied inside a container too."""
        payload = b'[["TaggedArrayOuter", {"a": "bad"}, 1]]'

        result, errors = load_json_with_default(payload, List[TaggedArrayOuter])

        assert result == [TaggedArrayOuter(inner=ArrayInner(a=0), x=1)]
        assert [e.loc for e in errors] == [(0, 1, 'a')]

    def test_in_a_dict(self):
        payload = b'{"k": ["TaggedArrayOuter", {"a": 1}, "bad"]}'

        result, errors = load_json_with_default(payload, Dict[str, TaggedArrayOuter])

        assert result == {'k': TaggedArrayOuter(inner=ArrayInner(a=1), x=0)}
        assert [e.loc for e in errors] == [('k', 2)]

    @pytest.mark.parametrize('model', [Optional[TaggedArrayOuter], Union[TaggedArrayOuter, int]],
                             ids=['optional', 'union'])
    def test_through_a_union(self, model):
        """The path is relative to the member, its positions still include the tag."""
        payload = b'["TaggedArrayOuter", {"a": "bad"}, 1]'

        result, errors = load_json_with_default(payload, model)

        assert result == TaggedArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(1, 'a')]

    def test_an_inherited_field(self):
        """Inherited fields come first, the tag still holds position 0."""
        payload = b'["TaggedChild", {"a": "bad"}, {"a": 2}]'

        result, errors = load_json_with_default(payload, TaggedChild)

        assert result == TaggedChild(base=ArrayInner(a=0), extra=ArrayInnerFive(a=2))
        assert [e.loc for e in errors] == [(1, 'a')]

    def test_a_field_the_child_adds(self):
        """Position 2 is the second field, its default is its own."""
        payload = b'["TaggedChild", {"a": 1}, "bad"]'

        result, errors = load_json_with_default(payload, TaggedChild)

        assert result == TaggedChild(base=ArrayInner(a=1), extra=ArrayInnerFive(a=5))
        assert [e.loc for e in errors] == [(2,)]

    def test_an_untagged_field_inside_a_tagged_one(self):
        """Only the outer array carries a tag, the inner one starts at its first field."""
        payload = b'["TaggedOfUntagged", [{"a": "bad"}, 1], 2]'

        result, errors = load_json_with_default(payload, TaggedOfUntagged)

        assert result == TaggedOfUntagged(untagged=ArrayOuter(inner=ArrayInner(a=0), x=1), y=2)
        assert [e.loc for e in errors] == [(1, 0, 'a')]

    def test_a_tagged_field_inside_an_untagged_one(self):
        """The inner array is the one with the tag, so its first field is position 1."""
        payload = b'[["TaggedArrayOuter", {"a": "bad"}, 1], 2]'

        result, errors = load_json_with_default(payload, UntaggedOfTagged)

        assert result == UntaggedOfTagged(tagged=TaggedArrayOuter(inner=ArrayInner(a=0), x=1), y=2)
        assert [e.loc for e in errors] == [(0, 1, 'a')]

    def test_msgpack(self):
        payload = msgspec.msgpack.encode(['TaggedArrayOuter', {'a': 1}, 'bad'])

        result, errors = load_msgpack_with_default(payload, TaggedArrayOuter)

        assert result == TaggedArrayOuter(inner=ArrayInner(a=1), x=0)
        assert [e.loc for e in errors] == [(2,)]

    def test_the_detail_reads_the_same_positions(self):
        """Both families agree on which field a position names."""
        payload = b'["TaggedArrayOuter", {"a": 1}, "bad"]'
        obj, detail = load_json_with_error_detail(payload, TaggedArrayOuter)
        result, errors = load_json_with_default(payload, TaggedArrayOuter)

        assert obj is NODEFAULT
        assert detail.loc == errors[0].loc == (2,)
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True


# --- A generic array kind: the position type comes from the alias ---


class TestGenericArrayKind:
    """A generic `array_like` struct keeps its type arguments in the field list.

    `origin_args()` splits `Box[Leaf]` into `Box` and `(Leaf,)`, and a field
    declared as `T` resolves only with the arguments put back: the bare `Box`
    still holds the `~T` it was declared with. Both families used to lose the
    type one level down, so the location was right while the offending value
    stayed unreadable and the repair fell back to the root default.
    """

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    def test_at_root(self, guess_default):
        result, errors = load_json_with_default(
            b'[{"a": "bad"}]', GenericBox[GenericLeaf], guess_default=guess_default)

        assert result == GenericBox(x=GenericLeaf(a=0))
        assert [e.loc for e in errors] == [(0, 'a')]

    def test_as_a_struct_field(self):
        """The field around the array keeps its own value."""
        payload = b'{"box": [{"a": "bad"}], "n": 1}'

        result, errors = load_json_with_default(payload, GenericHolder)

        assert result == GenericHolder(box=GenericBox(x=GenericLeaf(a=0)), n=1)
        assert [e.loc for e in errors] == [('box', 0, 'a')]

    def test_in_a_dict(self):
        result, errors = load_json_with_default(
            b'{"k": [{"a": "bad"}]}', Dict[str, GenericBox[GenericLeaf]])

        assert result == {'k': GenericBox(x=GenericLeaf(a=0))}
        assert [e.loc for e in errors] == [('k', 0, 'a')]

    def test_in_a_list(self):
        """A valid sibling entry is kept."""
        payload = b'[[{"a": "bad"}], [{"a": 2}]]'

        result, errors = load_json_with_default(payload, List[GenericBox[GenericLeaf]])

        assert result == [
            GenericBox(x=GenericLeaf(a=0)),
            GenericBox(x=GenericLeaf(a=2)),
        ]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_two_array_levels(self):
        """Each position resolves its own type argument."""
        result, errors = load_json_with_default(
            b'[[{"a": "bad"}]]', GenericBox[GenericBox[GenericLeaf]])

        assert result == GenericBox(x=GenericBox(x=GenericLeaf(a=0)))
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_the_detail_reads_the_value(self):
        obj, detail = load_json_with_error_detail(b'[{"a": "bad"}]', GenericBox[GenericLeaf])

        assert obj is NODEFAULT
        assert detail.loc == (0, 'a')
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_the_detail_agrees_with_the_repair(self):
        payload = b'[[{"a": "bad"}], [{"a": 2}]]'
        obj, detail = load_json_with_error_detail(payload, List[GenericBox[GenericLeaf]])
        result, errors = load_json_with_default(payload, List[GenericBox[GenericLeaf]])

        assert obj is NODEFAULT
        assert detail.loc == errors[0].loc == (0, 0, 'a')
        assert result == [GenericBox(x=GenericLeaf(a=0)), GenericBox(x=GenericLeaf(a=2))]

    def test_resolve_error(self):
        """An ErrorInfo built by hand walks the same path."""
        detail = resolve_error(
            ErrorInfo(msg='m', type=ErrorType.TYPE_MISMATCH, loc=(0, 'a')),
            [{'a': 'bad'}], GenericBox[GenericLeaf])

        assert detail.loc == (0, 'a')
        assert detail.loc_resolved is True
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_a_failing_position_itself(self):
        """The boundary that always worked: the position is the whole error."""
        result, errors = load_json_with_default(b'["bad"]', GenericBox[int], guess_default=True)

        assert result == GenericBox(x=0)
        assert [e.loc for e in errors] == [(0,)]

    def test_a_position_without_a_default_is_not_written(self):
        """`x: T` declares no default, so without guessing there is nothing to write."""
        result, errors = load_json_with_default(b'["bad"]', GenericBox[int])

        assert result is NODEFAULT
        assert [e.loc for e in errors] == [(0,)]

    def test_msgpack(self):
        payload = msgspec.msgpack.encode([[{'a': 'bad'}], [{'a': 2}]])

        result, errors = load_msgpack_with_default(payload, List[GenericBox[GenericLeaf]])

        assert result == [GenericBox(x=GenericLeaf(a=0)), GenericBox(x=GenericLeaf(a=2))]
        assert [e.loc for e in errors] == [(0, 0, 'a')]

    def test_convert(self):
        data = [{'a': 'bad'}]

        result, errors = convert_with_default(data, GenericBox[GenericLeaf])

        assert result == GenericBox(x=GenericLeaf(a=0))
        assert [e.loc for e in errors] == [(0, 'a')]
        assert data == [{'a': 0}]

    def test_a_non_generic_array_kind_is_untouched(self):
        """The control: an `array_like` struct with no type arguments."""
        result, errors = load_json_with_default(b'[{"a": "bad"}, 1]', ArrayOuter)

        assert result == ArrayOuter(inner=ArrayInner(a=0), x=1)
        assert [e.loc for e in errors] == [(0, 'a')]

    def test_a_generic_object_kind_is_untouched(self):
        """The other control: a generic struct addressed by field name."""
        payload = b'{"box": {"x": {"a": "bad"}}}'

        result, errors = load_json_with_default(payload, GenericObjectHolder)

        assert result == GenericObjectHolder(box=GenericObject(x=GenericLeaf(a=0)))
        assert [e.loc for e in errors] == [('box', 'x', 'a')]


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
