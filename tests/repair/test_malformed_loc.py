"""Adversarial tests: repair must survive a `loc` that doesn't match data / model.

In normal operation ``(raw data, model, error.loc)`` are consistent — msgspec
emits an accurate error path and our parser turns it into ``ErrorInfo.loc``.
A bug in msgspec (a wrong message) or in our path parser can however produce a
``loc`` that has nothing to do with either the input data or the validation
model.

These tests feed such corrupted combinations into the repair engine, the public
repair API and the `*_with_error_detail` family. The contract under test is:

1. no public function raises, whatever the combination is;
2. repair still does its best: the loop terminates and the result is either
   ``NODEFAULT`` or a valid instance of the model;
3. a walk that can not be completed reports no value instead of a wrong one.
"""
import copy
from typing import Dict, List, NamedTuple, Set, Tuple

import msgspec.msgpack
import pytest
from msgspec import NODEFAULT, UNSET, Struct, ValidationError, convert

import msgspecerror.const
import msgspecerror.repair as repair_mod
from msgspecerror import convert_with_error_detail, resolve_error
from msgspecerror.const import MSGSPEC_0_19_PLUS, ErrorType
from msgspecerror.parse_error import ErrorInfo
from msgspecerror.repair import (
    _handle_obj_repair, _pin_raw, _repair_once, _validate_by_decode, _validate_entry, load_json_with_default,
    load_msgpack_with_default
)

# --- Test Models ---


class Repairable(Struct):
    """Every field has a default, a root fallback is always possible."""
    a: int = 42
    b: str = 'x'
    c: List[int] = []


class Required(Struct):
    """No field has a default, repair can only fail to NODEFAULT."""
    a: int
    b: str


class Inner(Struct):
    x: int = 1


class Outer(Struct):
    inner: Inner
    tag: str = 't'


class TupleField(Struct):
    t: Tuple[int, str] = (0, '')


class DictOfTuple(Struct):
    """A dict of variable-length tuples, for nested `...` placeholder paths."""
    d: Dict[str, Tuple[int, ...]] = {}


class ArrayLike(Struct, array_like=True):
    """Decoded from an array, a position addresses its fields by order."""
    a: int = 0
    b: str = ''


class Pair(NamedTuple):
    """Always decoded from an array, like an `array_like` struct."""
    a: int
    b: str


class ListSubclass(list):
    """A list subclass is still an array to `convert`.

    Empty on purpose: the point is the type, not the behaviour.
    """


class SourceObject:
    """The source of `convert(..., from_attributes=True)`."""

    def __init__(self, a='bad'):
        self.a = a


class EmptyObject:
    """A source object missing the field the error names.

    Empty on purpose: every lookup raises `AttributeError`.
    """


# --- Adversarial (model, raw data, loc) triples ---
# The loc of each entry is deliberately unrelated to (model, raw data), as if
# msgspec or the path parser returned a corrupt path.
REPAIR_ONCE_CASES = [
    # loc names a field / key that doesn't exist
    ('unknown-struct-field', Repairable, {'a': 0, 'b': 'ok'}, ('ghost',)),
    ('unknown-struct-field-deep', Repairable, {'a': 0}, ('ghost', 'deeper')),
    ('unknown-nested-field', Outer, {'inner': {'x': 1}, 'tag': 't'}, ('inner', 'ghost')),
    ('unknown-dict-key', Dict[str, int], {'ok': 1}, ('ghost',)),
    ('empty-field-name', Repairable, {'': 1}, ('',)),
    ('unicode-field-name', Repairable, {'a': 0}, ('字段',)),

    # `...` placeholder implies a parameterized dict model, the models below are not
    ('placeholder-on-struct', Repairable, {'a': 0}, ('...',)),
    ('placeholder-on-required-struct', Required, {'a': 0}, ('...',)),
    ('placeholder-on-bare-dict', dict, {'k': 'v'}, ('...',)),
    ('placeholder-on-bare-list', list, [1, 2], ('...',)),
    ('placeholder-on-int', int, {}, ('...',)),
    ('placeholder-on-variadic-tuple', Tuple[int, ...], {'k': 'v'}, ('...',)),
    ('placeholder-on-empty-tuple', Tuple[()], {'k': 'v'}, ('...',)),
    ('placeholder-not-last', Repairable, {'a': 0}, ('...', 'ghost')),
    ('placeholder-on-scalar', Repairable, {'a': 'text'}, ('a', '...')),
    ('placeholder-no-failing-value', Dict[str, int], {'ok': 1}, ('...',)),

    # `...key` placeholder on models that have no dict key type
    ('key-placeholder-on-struct', Repairable, {'a': 0}, ('...key',)),
    ('key-placeholder-on-empty-tuple', Tuple[()], {'a': 0}, ('...key',)),
    ('key-placeholder-not-last', Dict[int, str], {'1': 'v'}, ('...key', 'ghost')),
    ('key-placeholder-no-failing-key', Dict[str, int], {'1': 'v'}, ('...key',)),

    # a list index where data / model is not a list
    ('index-on-struct', Repairable, {'a': 0}, (0,)),
    ('index-on-dict', List[int], {'0': 1}, (0,)),
    ('index-out-of-range', List[int], [1], (5,)),
    ('index-negative-out-of-range', List[int], [1], (-5,)),
    ('index-negative-in-range', List[int], [1, 2], (-1,)),
    ('index-huge', List[int], [1, 2], (10 ** 18,)),
    ('index-deep-out-of-range', List[Dict[str, int]], [{'ok': 1}], (3, 'ghost')),

    # a struct field where data is not a dict
    ('field-on-list', Repairable, [1, 2], ('a',)),
    ('field-on-scalar', Repairable, 'text', ('a',)),
    ('field-on-none', Repairable, None, ('a',)),
    ('field-deep-on-scalar', Repairable, {'a': 0}, ('a', 'ghost')),
    ('index-into-scalar', Repairable, {'a': 0}, ('a', 0)),
    ('field-into-list-field', Repairable, {'c': [1]}, ('c', 'ghost')),
    ('tuple-index-on-list-field', TupleField, {'t': [1, 'ok']}, ('t', 0)),

    # loc on models that are not containers at all
    ('field-on-int-model', int, 5, ('ghost',)),
    ('index-on-str-model', str, 'text', (0,)),
    ('placeholder-on-fixed-tuple-model', Tuple[int, str], [1, 'ok'], ('...',)),

    # pathological paths
    ('very-long-path', Repairable, {'a': 0}, ('ghost',) * 64),
    ('very-long-field-name', Repairable, {'a': 0}, ('鬼' * 1000,)),
    # `loc` is typed as a tuple of int / str, but a parser bug could still hand
    # over an object that can't be used as a dict key or field name
    ('none-loc-part', Repairable, {'a': 0}, (None,)),
    ('bool-loc-part', Repairable, {'a': 0}, (True,)),
    ('unhashable-loc-part', Repairable, {'a': {'b': 1}}, (['ghost'], 'deeper')),
]


class TestRepairOnceMalformedLoc:
    """`_repair_once` with a loc that doesn't match the data / model."""

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('name,model,data,loc', REPAIR_ONCE_CASES)
    def test_never_raises_and_returns_a_well_formed_pair(
            self, name, model, data, loc, guess_default
    ):
        """Any corrupted triple returns (obj, error), nothing propagates."""
        raw_obj = copy.deepcopy(data)
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=loc)

        result_obj, result_error = _repair_once(
            raw_obj, model, error, guess_default=guess_default)

        assert result_error is NODEFAULT or isinstance(result_error, ErrorInfo)
        if isinstance(result_error, ErrorInfo):
            assert isinstance(result_error.loc, tuple)
        if result_obj is NODEFAULT:
            # A failed repair always reports why instead of raising
            assert isinstance(result_error, ErrorInfo)


# --- Adversarial error messages ---
# Paths that msgspec (or our parser) would not produce for the data below.
MALFORMED_MESSAGES = [
    ('no-path', 'Expected `int`, got `str`'),
    ('unrelated-field', 'Expected `int`, got `str` - at `$.ghost`'),
    ('unrelated-deep-field', 'Expected `int`, got `str` - at `$.ghost.deeper`'),
    ('placeholder-at-root', 'Expected `int`, got `str` - at `$...`'),
    ('placeholder-deep', 'Expected `int`, got `str` - at `$.[...].x`'),
    ('dict-key-error', 'Expected `str`, got `int` - at `key` in `$.ghost`'),
    ('list-index-on-object', 'Expected `int`, got `str` - at `$[0]`'),
    ('huge-index', 'Expected `int`, got `str` - at `$.a[999999]`'),
    ('weird-bracket', 'Expected `int`, got `str` - at `$.a[abc]`'),
    ('double-dot-path', 'Expected `int`, got `str` - at `$..`'),
    ('root-dot-path', 'Expected `int`, got `str` - at `$.`'),
    ('unterminated-path', 'Expected `int`, got `str` - at `$.ghost'),
    ('unknown-field', 'Object contains unknown field `ghost` - at `$.ghost`'),
    ('missing-field', 'Object missing required field `ghost` - at `$.ghost`'),
    # msgspec 0.19.0 bug: the path suffix is missing from the message
    ('msgspec-missing-path-bug', 'Expected `array` of at most length 2'),
]

# The messages above are applied to data that is unrelated to the message path.
HANDLE_CASES = [
    (Repairable, {'a': 'bad', 'b': 'ok'}),
    (Repairable, {}),
    (Repairable, {'a': {'b': 1}}),
    (Required, {'a': 'bad'}),
    (Required, {}),
    (Repairable, [1, 2]),
    (Repairable, 'text'),
    (Outer, {'inner': {'x': 'bad'}, 'tag': 't'}),
    (Dict, {'ok': 1, 'bad': 'x'}),
    (Dict[str, int], {'ok': 1, 'bad': 'x'}),
    (Dict[int, str], {'bad': 'v'}),
    (List[int], [1, 'bad']),
    (Tuple[int, str], [1, 'ok']),
]


class TestHandleObjRepairMalformedLoc:
    """`_handle_obj_repair` with a message whose path is unrelated to data / model."""

    @pytest.mark.parametrize('guess_default', [False, True], ids=['no-guess', 'guess'])
    @pytest.mark.parametrize('model,data', HANDLE_CASES)
    @pytest.mark.parametrize('name,message', MALFORMED_MESSAGES)
    def test_never_raises_and_result_is_valid(self, name, message, model, data, guess_default):
        """Repair terminates within the cap and returns a valid instance or NODEFAULT."""
        result, errors = _handle_obj_repair(
            copy.deepcopy(data), model, ValidationError(message), guess_default=guess_default)

        assert isinstance(errors, list)
        assert all(isinstance(e, ErrorInfo) for e in errors)
        assert len(errors) <= msgspecerror.const.MAXIMUM_REPAIR + 1
        if result is not NODEFAULT:
            # A non-NODEFAULT result must be accepted by the model it claims to be
            assert convert(result, model) == result


class TestPlaceholderModelMismatch:
    """`...` / `...key` require a parameterized dict model.

    These combinations used to raise ``TypeError`` (``model_args`` is None,
    ``Ellipsis``, or the ``Tuple[()]`` args artifact); they must fail softly.
    """

    @pytest.mark.parametrize('model,loc', [
        (Repairable, ('...',)),
        (Repairable, ('...key',)),
        (dict, ('...',)),
        (dict, ('...key',)),
        (int, ('...',)),
        (str, ('...key',)),
        (list, ('...',)),
        (Tuple[int, ...], ('...',)),
        (Tuple[()], ('...',)),
        (Tuple[()], ('...key',)),
    ])
    def test_placeholder_on_non_dict_model_fails_softly(self, model, loc):
        """The unrepairable triple gives up with NODEFAULT instead of raising."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=loc)

        result_obj, result_error = _repair_once({'a': 1}, model, error)

        assert result_obj is NODEFAULT
        assert isinstance(result_error, ErrorInfo)

    def test_placeholder_nested_in_dict_entry_pops_the_entry(self):
        """A `...` that can't be walked pops the failing dict entry and keeps the valid sibling."""
        data = {'d': {'ok': [1, 2], 'bad': {'q': 1}}}
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=('d', '...', '...'))

        result_obj, result_error = _repair_once(copy.deepcopy(data), DictOfTuple, error)

        assert result_obj == {'d': {'ok': [1, 2]}}
        assert result_error.loc == ('d', 'bad', '...')

        result, errors = _handle_obj_repair(
            copy.deepcopy(data), DictOfTuple,
            ValidationError('Expected `int`, got `str` - at `$.d[...][...]`'))
        assert result == DictOfTuple(d={'ok': (1, 2)})
        assert len(errors) == 1


class TestPublicApiMalformedLoc:
    """The public loaders must survive a parser that returns a corrupted `loc`."""

    CORRUPTIONS = [
        ('unrelated', lambda loc: ('ghost', 'field', 7)),
        ('shifted', lambda loc: ('b',) + loc),
        ('truncated', lambda loc: ()),
        ('extended', lambda loc: loc + ('ghost', 'deep')),
        ('reversed', lambda loc: tuple(reversed(loc))),
        ('huge-index', lambda loc: loc + (10 ** 9,)),
    ]

    @pytest.mark.parametrize('corruption,corrupt', CORRUPTIONS,
                             ids=[name for name, _ in CORRUPTIONS])
    @pytest.mark.parametrize('data', [
        b'{"a": "bad", "b": "ok"}',
        b'{"c": ["x"], "b": "ok"}',
        b'{"c": "not-a-list"}',
        b'[1, 2]',
    ])
    def test_load_json_with_default_never_raises(self, monkeypatch, corruption, corrupt, data):
        """A corrupted loc must not break the JSON repair pipeline."""
        real_parse = repair_mod.parse_msgspec_error

        def corrupted_parse(error):
            info = real_parse(error)
            info.loc = corrupt(info.loc)
            return info

        monkeypatch.setattr(repair_mod, 'parse_msgspec_error', corrupted_parse)

        result, errors = load_json_with_default(data, Repairable)

        assert isinstance(result, Repairable)
        assert all(isinstance(e, ErrorInfo) for e in errors)

    def test_load_msgpack_with_default_never_raises(self, monkeypatch):
        """A corrupted loc must not break the msgpack repair pipeline."""
        real_parse = repair_mod.parse_msgspec_error

        def corrupted_parse(error):
            info = real_parse(error)
            info.loc = ('ghost', 'field', 7)
            return info

        monkeypatch.setattr(repair_mod, 'parse_msgspec_error', corrupted_parse)

        data = msgspec.msgpack.encode({'a': 'bad', 'b': 'ok'})
        result, errors = load_msgpack_with_default(data, Repairable)

        assert isinstance(result, Repairable)
        assert all(isinstance(e, ErrorInfo) for e in errors)

    def test_valid_data_never_touches_the_error_parser(self, monkeypatch):
        """A broken parser is irrelevant while the input validates on the first try."""

        def explode(error):
            raise AssertionError('parse_msgspec_error must not be called for valid input')

        monkeypatch.setattr(repair_mod, 'parse_msgspec_error', explode)

        result, errors = load_json_with_default(b'{"a": 1, "b": "ok"}', Repairable)

        assert result == Repairable(a=1, b='ok')
        assert errors == []


class TestFallbackValidity:
    """A fallback result must be valid for the model, never a wrong-typed value.

    A loc that doesn't match the data used to fall back to `()` for fixed-length
    tuples, which fails validation for e.g. ``Tuple[int, str]``.
    """

    def test_fixed_tuple_returns_nodefault_without_guessing(self):
        """Without guessing there is no valid default for `Tuple[int, str]`."""
        result, errors = load_json_with_default(b'[1]', Tuple[int, str])

        assert result is NODEFAULT
        assert len(errors) == 1

    def test_fixed_tuple_is_defaulted_item_by_item_when_guessing(self):
        """With guess_default=True each item is guessed by its own type."""
        result, errors = load_json_with_default(b'[1]', Tuple[int, str], guess_default=True)

        assert result == (0, '')
        assert len(errors) == 1

    @pytest.mark.parametrize('guess,expected', [
        (False, (2,)),
        (True, (0, 2)),
    ])
    def test_variadic_tuple_is_deleted_or_guessed_per_item(self, guess, expected):
        """Variable-length tuples keep their per-item repair behaviour."""
        result, errors = load_json_with_default(b'["bad", 2]', Tuple[int, ...], guess_default=guess)

        assert result == expected
        assert len(errors) == 1

    def test_empty_tuple_model_still_valid(self):
        """`Tuple[()]` still defaults to `()`, valid input is untouched."""
        result, errors = load_json_with_default(b'[]', Tuple[()])

        assert result == ()
        assert errors == []

        result, errors = load_json_with_default(b'[1]', Tuple[()])

        assert result == ()
        assert len(errors) == 1


class TestIndexSegmentAgainstModel:
    """An index only addresses a sequence or an array-decoded model.

    The data alone is not enough: msgspec never reports `$[0]` for a dict or a
    scalar model, so such a triple is a mismatch. Walking it anyway reads the
    model's args as if they were the item type and hands back an item the model
    never looked at.
    """

    @pytest.mark.parametrize('model', [Dict[str, int], Dict[str, List[int]], int, str],
                             ids=['dict', 'dict-of-list', 'int', 'str'])
    def test_a_non_sequence_model_has_no_item(self, model):
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(0,))

        detail = resolve_error(error, ['x'], model)

        assert detail.loc == (0,)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    @pytest.mark.parametrize('model,data', [
        (List[int], ['x']),
        (Tuple[int, int], ['x']),
        (Set[int], ['x']),
        (frozenset, ['x']),
    ], ids=['list', 'tuple', 'set', 'frozenset'])
    def test_a_sequence_model_has_one(self, model, data):
        """msgspec accepts a list as the input of every sequence model."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(0,))

        detail = resolve_error(error, data, model)

        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_an_array_like_struct_has_one(self):
        """An `array_like` struct is decoded from an array, the index is its field order."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(0,))

        detail = resolve_error(error, ['x', 1], ArrayLike)

        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_a_namedtuple_has_one(self):
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(1,))

        detail = resolve_error(error, [1, 2], Pair)

        assert detail.error_value == 2
        assert detail.error_value_resolved is True


class TestSequenceData:
    """`convert` takes any sequence as an array, the walk reads the same ones.

    A tuple, a NamedTuple and a list subclass all validate as `List[int]`, so a
    `$[1]` path against any of them names a real item.
    """

    @pytest.mark.parametrize('data', [
        (1, 'x'),
        Pair(1, 'x'),
        ListSubclass([1, 'x']),
    ], ids=['tuple', 'namedtuple', 'list-subclass'])
    def test_the_item_is_reached(self, data):
        obj, detail = convert_with_error_detail(data, List[int])

        assert obj is NODEFAULT
        assert detail.loc == (1,)
        assert detail.error_value == 'x'
        assert detail.error_value_resolved is True

    def test_a_plain_list_still_works(self):
        """The control case, so a broken walk can not pass the rows above."""
        obj, detail = convert_with_error_detail([1, 'x'], List[int])

        assert obj is NODEFAULT
        assert detail.loc == (1,)
        assert detail.error_value == 'x'


class TestFromAttributes:
    """`convert(..., from_attributes=True)` reads attributes, the walk too."""

    def test_the_value_is_read_from_the_attribute(self):
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=('a',))

        detail = resolve_error(error, SourceObject(), Repairable, from_attributes=True)

        assert detail.loc == ('a',)
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True

    def test_without_the_option_the_object_is_not_walked(self):
        """`convert` reads a dict and nothing else unless asked, so the walk does too."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=('a',))

        detail = resolve_error(error, SourceObject(), Repairable)

        assert detail.loc == ('a',)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_a_missing_attribute_keeps_the_path(self):
        """A field the object does not have is a missing value, not a crash."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=('a',))

        detail = resolve_error(error, EmptyObject(), Repairable, from_attributes=True)

        assert detail.loc == ('a',)
        assert detail.error_value is UNSET


class TestNegativeIndex:
    """msgspec reports a position, never Python's `-1` last item.

    `data[-1]` is a real item, but it is not the item the path names: a
    negative segment is always a mismatch.
    """

    def test_the_last_item_is_not_reported_as_the_offending_one(self):
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(-1,))

        detail = resolve_error(error, [1, 2], List[int])

        assert detail.loc == (-1,)
        assert detail.error_value is UNSET
        assert detail.error_value_resolved is False

    def test_a_deeper_negative_segment_does_not_resolve_either(self):
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(-1, 0))

        detail = resolve_error(error, [[1]], List[List[int]])

        assert detail.error_value is UNSET

    def test_the_repair_does_not_pop_the_last_item(self):
        """`obj.pop(-1)` would delete an item the error never named."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(-1,))

        result, result_error = _repair_once([1, 2], List[int], error)

        assert result is NODEFAULT
        assert result_error.loc == (-1,)

    def test_a_positive_index_is_still_repaired(self):
        """The control case: only a negative segment is rejected."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(0,))

        result, result_error = _repair_once(['bad', 2], List[int], error)

        assert result == [2]


class TestArrayLikeOutOfRange:
    """A position past the end of an `array_like` model can not be written.

    msgspec reports a root-level `Expected array of length N` for an array that
    is too short, so an in-range-looking index is only reachable from a
    corrupted loc.
    """

    def test_repair_once_gives_up(self):
        """The field default exists, but there is no slot to write it into."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(1,))

        result, result_error = _repair_once([1], ArrayLike, error)

        assert result is NODEFAULT
        assert result_error.loc == (1,)

    def test_an_in_range_index_is_still_repaired(self):
        """The control case: a position the data has is written as usual."""
        error = ErrorInfo(msg='fake', type=ErrorType.TYPE_MISMATCH, loc=(1,))

        result, result_error = _repair_once([1, 2], ArrayLike, error)

        assert result == [1, '']

    def test_the_public_api_survives_a_loc_past_the_end(self, monkeypatch):
        """A corrupted loc must not turn the repair pipeline into a raise."""
        real_parse = repair_mod.parse_msgspec_error

        def corrupted_parse(error):
            info = real_parse(error)
            info.loc = (1,)
            return info

        monkeypatch.setattr(repair_mod, 'parse_msgspec_error', corrupted_parse)

        result, errors = load_json_with_default(b'[1]', ArrayLike)

        assert isinstance(result, ArrayLike)
        assert all(isinstance(e, ErrorInfo) for e in errors)


class RawHolder(Struct):
    """A raw field plus a field that can fail."""
    payload: msgspec.Raw
    n: int = 0


class PlainHolder(Struct):
    """The same shape without the raw field."""
    n: int = 0


class TestPinRaw:
    """`_pin_raw` replaces one spot on a copy, and never raises on a bad loc."""

    def test_pins_without_touching_the_input(self):
        data = {'payload': {'a': 1}, 'n': 1}
        snapshot = copy.deepcopy(data)
        pinned = _pin_raw(data, ('payload',), RawHolder)

        assert isinstance(pinned['payload'], msgspec.Raw)
        assert pinned['n'] == 1
        assert data == snapshot

    def test_pins_every_dict_value(self):
        data = {'a': {'x': 1}, 'b': {'y': 2}}
        pinned = _pin_raw(data, ('...',), Dict[str, msgspec.Raw])

        assert all(isinstance(value, msgspec.Raw) for value in pinned.values())

    def test_pins_every_list_item(self):
        data = [{'a': 1}, {'b': 2}]
        pinned = _pin_raw(data, (0,), List[msgspec.Raw])

        assert all(isinstance(value, msgspec.Raw) for value in pinned)

    def test_pins_one_item_of_a_fixed_tuple(self):
        model = Tuple[msgspec.Raw, int]
        pinned = _pin_raw([{'a': 1}, 5], (0,), model)

        assert isinstance(pinned[0], msgspec.Raw)
        assert pinned[1] == 5

    @pytest.mark.parametrize('model', [RawHolder, Dict[str, RawHolder], PlainHolder, None, 12345])
    @pytest.mark.parametrize('loc', [
        ('ghost',), ('...', 0), (0,), (99,), (None,), (['x'],), ('payload', 'deeper'),
    ])
    def test_never_raises(self, model, loc):
        data = {'payload': {'a': 1}, 'n': 1}
        snapshot = copy.deepcopy(data)
        _pin_raw(data, loc, model)
        assert data == snapshot


class TestValidateEntry:
    """`_validate_entry` answers pass / the real error / unknown, never raises."""

    @pytest.mark.skipif(
        not MSGSPEC_0_19_PLUS,
        reason='msgspec < 0.19.0 refuses a Raw instance in convert, pinning can not settle an entry',
    )
    def test_passes_after_pinning(self):
        assert _validate_entry({'payload': {'a': 1}, 'n': 1}, RawHolder, {}) is None

    @pytest.mark.skipif(
        not MSGSPEC_0_19_PLUS,
        reason='msgspec < 0.19.0 refuses a Raw instance in convert, pinning can not settle an entry',
    )
    def test_reports_the_real_failure_behind_the_raw_field(self):
        error = _validate_entry({'payload': {'a': 1}, 'n': 'bad'}, RawHolder, {})
        assert error.loc == ('n',)
        assert error.type is ErrorType.TYPE_MISMATCH

    def test_reports_a_missing_field(self):
        error = _validate_entry({'n': 1}, RawHolder, {})
        assert error.loc == ('payload',)
        assert error.type is ErrorType.MISSING_FIELD

    def test_reports_a_plain_failure(self):
        error = _validate_entry({'n': 'bad'}, PlainHolder, {})
        assert error.loc == ('n',)
        assert error.type is ErrorType.TYPE_MISMATCH

    def test_returns_none_for_a_valid_entry(self):
        assert _validate_entry({'n': 1}, PlainHolder, {}) is None

    @pytest.mark.parametrize('model', [12345, object(), 'int'])
    def test_unknown_model_is_not_a_verdict(self, model):
        from msgspec import NODEFAULT
        assert _validate_entry({'n': 1}, model, {}) is NODEFAULT

    def test_a_pin_that_changes_nothing_falls_back_to_a_decode(self, monkeypatch):
        """msgspec 0.18.6 refuses a `Raw` instance, so the value is decoded again.

        The loc stays the same after pinning, which means the pin was not the
        problem: this msgspec never takes a `Raw` instance in `convert`. The
        typed decoder does take the payload bytes, so it judges the value.
        """
        import msgspecerror.repair as repair_mod

        monkeypatch.setattr(repair_mod, '_pin_raw', lambda value, loc, model: value)
        assert _validate_entry({'payload': {'a': 1}, 'n': 1}, RawHolder, {}) is None

    def test_the_decode_fallback_still_reports_a_real_error(self, monkeypatch):
        import msgspecerror.repair as repair_mod

        monkeypatch.setattr(repair_mod, '_pin_raw', lambda value, loc, model: value)
        error = _validate_entry({'payload': {'a': 1}, 'n': 'bad'}, RawHolder, {})
        assert error.loc == ('n',)


class TestValidateByDecode:
    """The decode fallback answers pass / the real error / unknown."""

    def test_passes_when_only_the_raw_field_complains(self):
        assert _validate_by_decode({'payload': {'a': 1}, 'n': 1}, RawHolder, {}) is None

    def test_reports_the_real_error(self):
        error = _validate_by_decode({'payload': {'a': 1}, 'n': 'bad'}, RawHolder, {})
        assert error.loc == ('n',)
        assert error.type is ErrorType.TYPE_MISMATCH

    def test_a_value_msgpack_can_not_carry_is_unknown(self):
        from msgspec import NODEFAULT
        assert _validate_by_decode({'n': object()}, PlainHolder, {}) is NODEFAULT

    @pytest.mark.parametrize('model', [12345, object(), 'int'])
    def test_unknown_model_is_not_a_verdict(self, model):
        from msgspec import NODEFAULT
        assert _validate_by_decode({'n': 1}, model, {}) is NODEFAULT

    def test_the_convert_only_options_are_not_forwarded(self):
        """`str_keys` is a `convert` option, `decode` would reject it."""
        assert _validate_by_decode({'payload': {'a': 1}, 'n': 1}, RawHolder, {'str_keys': True}) is None
