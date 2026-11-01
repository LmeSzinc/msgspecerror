"""What msgspec reads as an object / as an array, the library must read the same way.

A struct field and a `Dict` entry are looked up in the raw data. msgspec takes
a dict, a dict subclass (`OrderedDict`, `defaultdict`, `Counter`), a `UserDict`,
a `ChainMap` and a read-only mapping as well; a check for the exact `dict` type
made every one of them look like "the location and the data disagree", so the
detail family answered `loc=('...',)` with no value and the repair family gave
up on the whole object. A list subclass was treated the same way on the array
side while msgspec accepts it.

The two sides are deliberately different. Reading only needs a `Mapping` (an
array: a list or a tuple). Writing is a repair, and a repair that gives up has
to put the container back, which is only known to round-trip for a dict: a
`ChainMap.pop()` looks at the first map only, and `clear()` plus `update()`
moves the later maps into the first one. A tuple can not be written at all. So
a repair writes into a dict or a list (subclasses included) and gives up on
any other container, reporting the placeholder unchanged instead of a wrong
value.

`UserList`, `deque`, `array.array` and every other sequence are outside the
contract on purpose: msgspec rejects them at the array check itself, so no
error location ever points inside one. `_SEQUENCE_TYPES` in `repair.py` is the
one place to edit once msgspec starts taking one of them.
"""
from array import array
from collections import ChainMap, Counter, OrderedDict, UserDict, UserList, defaultdict, deque
from types import MappingProxyType
from typing import Dict, List, Tuple

import msgspec
import pytest
from msgspec import Struct, UNSET

from msgspecerror import convert_with_default, convert_with_error_detail
from msgspecerror.const import ErrorType
from msgspecerror.locate import (
    _SEQUENCE_TYPES, _WRITABLE_SEQUENCE_TYPES, _is_mapping, _is_sequence, _is_writable_mapping,
    _is_writable_sequence, _pin_raw
)

# `(id, constructor)` pairs. A constructor wraps the payload into the mapping
# under test; each case builds its own object, so a repair of one case can not
# leak into the next one.
MAPPINGS = [
    ('dict', dict),
    ('ordered-dict', OrderedDict),
    ('default-dict', lambda data: defaultdict(int, data)),
    ('counter', Counter),
    ('user-dict', UserDict),
    ('chain-map', ChainMap),
    ('mapping-proxy', MappingProxyType),
]
MAPPING_IDS = [name for name, _ in MAPPINGS]
# The mappings a repair may write into, see `_is_writable_mapping()`
WRITABLE_IDS = ('dict', 'ordered-dict', 'default-dict', 'counter')
READ_ONLY_IDS = tuple(name for name in MAPPING_IDS if name not in WRITABLE_IDS)


def build(name, data):
    """
    Wrap a plain dict into the mapping named by a test id.

    Args:
        name (str): One of `MAPPING_IDS`
        data (dict): Payload to wrap

    Returns:
        Any: The mapping under test
    """
    for case_name, constructor in MAPPINGS:
        if case_name == name:
            return constructor(data)
    raise KeyError(name)


class Doc(Struct):
    """A struct read from a mapping, its field holds a dict of ints."""
    entries: Dict[str, int] = {}


class TestPredicates:
    """The two predicates answer "is this read / written as an object"."""

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_every_mapping_is_read_as_an_object(self, name):
        assert _is_mapping(build(name, {})) is True

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_only_a_dict_is_written_into(self, name):
        assert _is_writable_mapping(build(name, {})) is (name in WRITABLE_IDS)

    @pytest.mark.parametrize('data', [[], [1], 'text', b'text', 1, None, object()])
    def test_a_non_mapping_is_never_an_object(self, data):
        assert _is_mapping(data) is False
        assert _is_writable_mapping(data) is False

    def test_a_duck_typed_mapping_is_not_claimed(self):
        """msgspec takes it, but `Mapping` does not, and a guess could go wrong."""

        class Duck:
            def keys(self):
                return {'k': 1}.keys()

            def __getitem__(self, key):
                return {'k': 1}[key]

        assert _is_mapping(Duck()) is False


class TestDetailFamilyReadsAMapping:
    """The `...` / `...key` placeholders are resolved inside any mapping."""

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_dict_value_is_resolved(self, name):
        _, detail = convert_with_error_detail(build(name, {'k': 'x', 'ok': 1}), Dict[str, int])

        assert detail.loc == ('k',)
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_dict_key_is_resolved(self, name):
        # A str key can not be converted to an int entry key
        _, detail = convert_with_error_detail(build(name, {'k': 'x'}), Dict[int, int])

        assert detail.error_value == 'k'
        assert detail.loc == ('k',)
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_struct_field_is_resolved(self, name):
        _, detail = convert_with_error_detail(build(name, {'entries': {'k': 'x'}}), Doc)

        assert detail.loc == ('entries', 'k')
        assert detail.error_value == 'x'
        assert detail.type is ErrorType.TYPE_MISMATCH
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True

    @pytest.mark.parametrize('name', MAPPING_IDS)
    def test_mapping_nested_in_a_mapping(self, name):
        """The root is a mapping, the failing entry is a mapping as well."""
        data = build(name, {'bad': {'k': 'x'}, 'ok': {'k': 1}})

        _, detail = convert_with_error_detail(data, Dict[str, Dict[str, int]])

        assert detail.loc == ('bad', 'k')
        assert detail.error_value == 'x'
        assert detail.loc_resolved is True
        assert detail.error_value_resolved is True


class TestRepairFamilyWritesADict:
    """A dict subclass is repaired in place, any other mapping is left alone."""

    @pytest.mark.parametrize('name', WRITABLE_IDS)
    def test_the_bad_entry_is_deleted(self, name):
        data = build(name, {'k': 'x', 'ok': 1})
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {'ok': 1}
        assert [e.loc for e in errors] == [('k',)]
        assert dict(data) == {'ok': 1}

    @pytest.mark.parametrize('name', WRITABLE_IDS)
    def test_the_surviving_entries_keep_their_order(self, name):
        data = build(name, {'a': 1, 'k': 'x', 'b': 2})
        convert_with_default(data, Dict[str, int])

        assert list(data) == ['a', 'b']

    @pytest.mark.parametrize('name', READ_ONLY_IDS)
    def test_a_read_only_mapping_is_not_claimed(self, name):
        """The repair would have to write into it, so it gives up instead."""
        data = build(name, {'k': 'x', 'ok': 1})
        result, errors = convert_with_default(data, Dict[str, int])

        assert result == {}
        assert [e.loc for e in errors] == [('...',)]
        assert dict(data) == {'k': 'x', 'ok': 1}

    @pytest.mark.parametrize('name', WRITABLE_IDS)
    def test_a_restored_mapping_keeps_its_order(self, name):
        """A repair that gives up puts every entry back at its original place."""

        class Required(Struct, forbid_unknown_fields=True):
            entries: Dict[str, int] = {}

        data = build(name, {'entries': {'k': 'x'}, 'z': 1})
        result, errors = convert_with_default(data, Required)

        assert result == Required(entries={})
        assert [e.loc for e in errors] == [('entries', 'k'), ('z',)]
        assert dict(data) == {'entries': {'k': 'x'}, 'z': 1}
        assert list(data) == ['entries', 'z']


class TestFabricatedKey:
    """A mapping can hand back a value for a key it never held.

    `defaultdict(int)` returns 0 for a missing field, but msgspec still reports
    `Object missing required field`, so the lookup must not be trusted: a
    fabricated value would be delivered as if it were the offending one.
    """

    class Required(Struct):
        a: int
        b: str = 'x'

    @pytest.mark.parametrize('name', ['default-dict', 'dict'])
    def test_a_missing_field_has_no_value(self, name):
        _, detail = convert_with_error_detail(build(name, {}), self.Required)

        assert detail.loc == ('a',)
        assert detail.error_value_resolved is False
        assert detail.error_value is UNSET

    @pytest.mark.parametrize('name', ['default-dict', 'dict'])
    def test_a_present_field_still_has_its_value(self, name):
        _, detail = convert_with_error_detail(build(name, {'a': 'bad'}), self.Required)

        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True


class TestSequencePredicates:
    """The two predicates answer "is this read / written as an array".

    `deque` and `UserList` are named on purpose: they are the types a user is
    most likely to hand over, and the ones this contract has to grow into if
    msgspec ever takes them.
    """

    class MyList(list):
        pass

    # `(id, a valid sample, whether msgspec takes it as an array)`
    SAMPLES = [
        ('list', [1, 2], True),
        ('list-subclass', MyList([1, 2]), True),
        ('tuple', (1, 2), True),
        ('set', {1, 2}, True),
        ('frozenset', frozenset({1, 2}), True),
        ('user-list', UserList([1, 2]), False),
        ('deque', deque([1, 2]), False),
        ('array-array', array('i', [1, 2]), False),
        ('range', range(2), False),
        ('str', 'ab', False),
        ('bytes', b'ab', False),
    ]
    SAMPLE_IDS = [name for name, _, _ in SAMPLES]

    @pytest.mark.parametrize('name,data,accepted', SAMPLES, ids=SAMPLE_IDS)
    def test_a_claimed_sequence_is_an_array_to_msgspec(self, name, data, accepted):
        """A claimed type must be one msgspec reads, or we would show it a value it skipped."""
        if not _is_sequence(data):
            return
        assert accepted is True
        assert msgspec.convert(data, List[int]) == [1, 2]

    @pytest.mark.parametrize('name,data,accepted', SAMPLES, ids=SAMPLE_IDS)
    def test_what_msgspec_rejects_is_never_claimed(self, name, data, accepted):
        if accepted:
            return
        assert _is_sequence(data) is False

    def test_a_set_is_not_claimed_even_though_msgspec_takes_it(self):
        """msgspec reports `$[1]` for a set, but its iteration order is not a position."""
        assert _is_sequence({1, 2}) is False
        assert _is_sequence(frozenset({1, 2})) is False

    def test_only_a_list_is_written_into(self):
        assert _is_writable_sequence([1]) is True
        assert _is_writable_sequence(self.MyList([1])) is True
        assert _is_writable_sequence((1,)) is False
        assert _is_writable_sequence(deque([1])) is False

    def test_a_deque_is_not_claimed_today(self):
        """msgspec rejects it at the array check, so no location ever points inside one."""
        assert _is_sequence(deque([1])) is False
        assert _is_writable_sequence(deque([1])) is False

    def test_the_writable_types_are_a_subset_of_the_readable_ones(self):
        """A type added to the writable set can not become readable by accident."""
        assert set(_WRITABLE_SEQUENCE_TYPES) <= set(_SEQUENCE_TYPES)


class TestSequenceSubclass:
    """A list subclass is a list to msgspec, so it is one to the repair too.

    Only `list` is written: msgspec rejects `UserList`, `deque` and any other
    sequence at the array check, so a failing item is never reported inside
    one and there is nothing to repair there.
    """

    class MyList(list):
        pass

    @pytest.mark.parametrize('factory', [list, MyList], ids=['list', 'list-subclass'])
    def test_a_bad_item_is_deleted(self, factory):
        data = factory([1, 'x'])
        result, errors = convert_with_default(data, List[int])

        assert result == [1]
        assert [e.loc for e in errors] == [(1,)]
        assert list(data) == [1]

    @pytest.mark.parametrize('factory', [list, MyList], ids=['list', 'list-subclass'])
    def test_a_bad_item_is_guessed(self, factory):
        data = factory([1, 'x'])
        result, errors = convert_with_default(data, List[int], guess_default=True)

        assert result == [1, 0]
        assert [e.loc for e in errors] == [(1,)]
        assert list(data) == [1, 0]

    def test_a_tuple_item_is_found_for_reading(self):
        """Reading a tuple is allowed, a tuple is what msgspec decodes to as well."""
        _, detail = convert_with_error_detail((1, 'x'), List[int])

        assert detail.loc == (1,)
        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_a_raw_field_inside_a_tuple_is_filled(self):
        """Reading a tuple covers the pinning walk, which copies the path it walks."""
        pinned = _pin_raw(({'a': 1}, 5), (0,), Tuple[msgspec.Raw, int])

        assert isinstance(pinned[0], msgspec.Raw)
        assert pinned[1] == 5
