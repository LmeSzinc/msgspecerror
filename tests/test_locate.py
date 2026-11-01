"""How the failing entry of a dict is found, at any size.

msgspec reports a failing dict entry as `$[...]` and a failing key as
``at `key` in `$` ``, neither of which says which key it was. The search used
to probe one entry at a time, which costs one `convert`, one built container
and one parsed message per entry: linear in the object with a large constant.

Both searches now have a bulk path, and a small object keeps the old one:

- values: the values are handed to msgspec as a list, which numbers its items,
  so one `convert` names the position and the position names the key;
- keys: each key is wrapped back into a one-entry dict, because the key
  coercion rules only apply to a dict key, and those wrappers go over as a
  list: msgspec numbers them the same way, so one `convert` names the key.

What matters here is that the two paths answer the same thing, that the bulk
path is exact about which key msgspec was complaining about, and that the
shortcuts never invent a key. `_BATCH_SEARCH_MINIMUM` is patched to force one
path or the other.
"""
from typing import Dict, List

import msgspec
import pytest
from msgspec import NODEFAULT, Struct

import msgspecerror.locate as locate_mod
from msgspecerror.locate import (
    _find_failing_dict_key, _find_failing_dict_key_each, _find_failing_dict_value
)


class Cell(Struct):
    v: int = 0
    name: str = 'x'


class RawReq(Struct):
    payload: msgspec.Raw
    n: int = 0


def with_batch(monkeypatch):
    """Force the bulk path on any size."""
    monkeypatch.setattr(locate_mod, '_BATCH_SEARCH_MINIMUM', 0)


def without_batch(monkeypatch):
    """Force the one-probe-at-a-time path on any size."""
    monkeypatch.setattr(locate_mod, '_BATCH_SEARCH_MINIMUM', 10 ** 9)


def cell_dict(n, bad_at):
    """`n` healthy entries, the one at `bad_at` fails."""
    data = {f'k{i}': {'v': i, 'name': 'x'} for i in range(n)}
    data[f'k{bad_at}']['v'] = 'bad'
    return data


def healthy_cell_dict(n):
    return {f'k{i}': {'v': i, 'name': 'x'} for i in range(n)}


SIZES = [1, 2, 4, 15, 16, 17, 32, 100]


class TestValueSearchFindsTheEntry:
    """The key that comes back is the entry msgspec was complaining about."""

    @pytest.mark.parametrize('where', ['first', 'middle', 'last'])
    @pytest.mark.parametrize('n', SIZES)
    def test_every_position(self, monkeypatch, n, where):
        with_batch(monkeypatch)
        bad_at = {'first': 0, 'middle': n // 2, 'last': n - 1}[where]

        assert _find_failing_dict_value(cell_dict(n, bad_at), Cell) == f'k{bad_at}'

    @pytest.mark.parametrize('where', ['first', 'middle', 'last'])
    @pytest.mark.parametrize('n', SIZES)
    def test_every_position_without_the_batch(self, monkeypatch, n, where):
        without_batch(monkeypatch)
        bad_at = {'first': 0, 'middle': n // 2, 'last': n - 1}[where]

        assert _find_failing_dict_value(cell_dict(n, bad_at), Cell) == f'k{bad_at}'

    @pytest.mark.parametrize('n', SIZES)
    def test_no_failing_entry_is_no_answer(self, monkeypatch, n):
        with_batch(monkeypatch)

        assert _find_failing_dict_value(healthy_cell_dict(n), Cell) is NODEFAULT

    def test_an_empty_object_is_no_answer(self, monkeypatch):
        with_batch(monkeypatch)

        assert _find_failing_dict_value({}, Cell) is NODEFAULT

    @pytest.mark.parametrize('n', [1, 4, 16, 40])
    def test_the_two_paths_agree(self, monkeypatch, n):
        """Whichever path runs, the answer is the same key."""
        data = cell_dict(n, n // 2)

        with_batch(monkeypatch)
        bulk = _find_failing_dict_value(data, Cell)

        without_batch(monkeypatch)
        one_by_one = _find_failing_dict_value(data, Cell)

        assert bulk == one_by_one == f'k{n // 2}'

    def test_a_later_entry_does_not_win(self):
        """The first failing entry in dict order is the one msgspec reported."""
        data = healthy_cell_dict(40)
        data['k5']['v'] = 'bad'
        data['k30']['v'] = 'also bad'
        data['k39']['name'] = 1

        assert _find_failing_dict_value(data, Cell) == 'k5'

    def test_an_insertion_order_is_not_a_sorted_order(self):
        data = {f'k{i}': {'v': i, 'name': 'x'} for i in [7, 3, 90, 1, 40, 20, 55, 2, 6, 8, 9, 10, 11, 12, 13, 14]}
        data['k90']['v'] = 'bad'

        assert _find_failing_dict_value(data, Cell) == 'k90'


class TestValueSearchKeepsTheRules:
    """The bulk probe must see the rules the failed call saw."""

    def test_strict_false_accepts_a_numeric_string(self):
        data = {f'k{i}': i for i in range(40)}
        data['k39'] = '39'

        assert _find_failing_dict_value(data, int, {'strict': True}) == 'k39'
        assert _find_failing_dict_value(data, int, {'strict': False}) is NODEFAULT

    def test_strict_false_with_the_batch_and_without_it(self, monkeypatch):
        data = {f'k{i}': i for i in range(40)}
        data['k39'] = '39'

        with_batch(monkeypatch)
        assert _find_failing_dict_value(data, int, {'strict': False}) is NODEFAULT
        without_batch(monkeypatch)
        assert _find_failing_dict_value(data, int, {'strict': False}) is NODEFAULT

    @pytest.mark.parametrize('model', [12345, 'int', object()])
    def test_an_unsupported_value_model_is_no_answer(self, monkeypatch, model):
        with_batch(monkeypatch)
        data = {f'k{i}': i for i in range(40)}

        assert _find_failing_dict_value(data, model) is NODEFAULT


class TestValueSearchAndRawFields:
    """A decoded object at a `Raw` field is an artifact of the probe.

    Judging a value as a list item reports that artifact like any other error,
    so the bulk answer can not be trusted there: the per-entry probe retries the
    entry with the raw field filled and reports what is really behind it.
    """

    def test_the_artifact_does_not_hide_the_real_error(self, monkeypatch):
        data = {f'k{i}': {'payload': {'a': i}, 'n': i} for i in range(40)}
        data['k39'] = {'payload': {'a': 39}, 'n': 'bad'}
        data['k0'] = {'payload': {'a': 0}, 'n': 0}

        assert _find_failing_dict_value(data, RawReq) == 'k39'

    def test_an_entry_whose_only_issue_is_raw_is_not_the_failing_one(self, monkeypatch):
        data = {f'k{i}': {'payload': {'a': i}, 'n': i} for i in range(40)}

        assert _find_failing_dict_value(data, RawReq) is NODEFAULT


def int_keys_with_bad_key(n, bad_at):
    """`n` int keys, the key at `bad_at` is a str that can not be an int key."""
    keys = [f'bad{bad_at}' if i == bad_at else i for i in range(n)]
    return {key: None for key in keys}


class TestDictionaryKeySearch:
    """The batched key search answers the same key as the per-key probe."""

    @pytest.mark.parametrize('n', SIZES)
    @pytest.mark.parametrize('where', ['first', 'middle', 'last'])
    def test_every_position(self, monkeypatch, n, where):
        with_batch(monkeypatch)
        bad_at = {'first': 0, 'middle': n // 2, 'last': n - 1}[where]
        data = int_keys_with_bad_key(n, bad_at)

        assert _find_failing_dict_key(data, int) == f'bad{bad_at}'

    @pytest.mark.parametrize('n', SIZES)
    @pytest.mark.parametrize('where', ['first', 'middle', 'last'])
    def test_every_position_without_the_batch(self, monkeypatch, n, where):
        without_batch(monkeypatch)
        bad_at = {'first': 0, 'middle': n // 2, 'last': n - 1}[where]
        data = int_keys_with_bad_key(n, bad_at)

        assert _find_failing_dict_key(data, int) == f'bad{bad_at}'

    @pytest.mark.parametrize('n', [1, 4, 16, 40])
    def test_the_two_paths_agree(self, monkeypatch, n):
        data = {i: None for i in range(n)}
        data['bad'] = None

        with_batch(monkeypatch)
        bulk = _find_failing_dict_key(data, int)
        without_batch(monkeypatch)
        one_by_one = _find_failing_dict_key(data, int)

        assert bulk == one_by_one == 'bad'

    def test_a_key_free_object_is_no_answer(self, monkeypatch):
        """A corrupt location can point at a `...key` segment with no bad key."""
        with_batch(monkeypatch)
        data = {i: None for i in range(40)}

        assert _find_failing_dict_key(data, int) is NODEFAULT

    def test_several_bad_keys_report_the_first(self, monkeypatch):
        with_batch(monkeypatch)
        data = {i: None for i in range(40)}
        data['bad-a'] = None
        data['bad-b'] = None

        keys = list(data)
        assert _find_failing_dict_key(data, int) == keys[40]

    def test_the_batch_still_uses_dict_key_rules(self, monkeypatch):
        """A json payload coerces string keys, a list item would not.

        Each key is wrapped back into a dict, so the coercion stays in play:
        the numeric strings pass and the answer is the one key that does not.
        """
        with_batch(monkeypatch)
        data = {str(i): None for i in range(40)}
        data['not-a-number'] = None

        assert _find_failing_dict_key(data, int, {'str_keys': True}) == 'not-a-number'

    def test_the_batch_without_str_keys_still_finds_the_first(self, monkeypatch):
        with_batch(monkeypatch)
        data = {i: None for i in range(40)}
        data['bad'] = None

        assert _find_failing_dict_key(data, int, {'str_keys': False}) == 'bad'

    @pytest.mark.parametrize('model', [12345, 'int', object()])
    def test_an_unsupported_key_model_is_no_answer(self, monkeypatch, model):
        with_batch(monkeypatch)
        data = {i: None for i in range(40)}

        assert _find_failing_dict_key(data, model) is NODEFAULT

    def test_the_single_probe_helper_answers_the_same(self, monkeypatch):
        without_batch(monkeypatch)
        data = {i: None for i in range(40)}
        data['bad'] = None

        assert _find_failing_dict_key_each(data, Dict[int, object], {}) == 'bad'

    def test_a_bare_key_list_would_answer_differently(self):
        """Why each key is wrapped back into a dict.

        `str_keys` coerces the string key of a dict to the key type, and a list
        item gets no such rule. Handing the bare keys over as a list would
        reject the first numeric string, which msgspec accepts as a dict key.
        """
        data = {str(i): None for i in range(40)}

        with pytest.raises(msgspec.ValidationError):
            msgspec.convert(list(data), List[int], str_keys=True)

        assert _find_failing_dict_key(data, int, {'str_keys': True}) is NODEFAULT

    def test_a_position_past_the_end_is_no_answer(self, monkeypatch):
        """The position comes from a message, a corrupt one must not raise."""
        with_batch(monkeypatch)
        monkeypatch.setattr(locate_mod, '_position_of', lambda loc: 10 ** 6)
        data = {f'k{i}': None for i in range(40)}
        data['bad'] = None

        assert _find_failing_dict_key(data, int) is NODEFAULT

    @pytest.mark.parametrize('loc', [(), (None,), ('x',), (-1,), (1.0,), (True,)])
    def test_a_location_without_a_position_is_no_answer(self, loc):
        assert locate_mod._position_of(loc) is NODEFAULT

    def test_a_normal_position_is_read_back(self):
        assert locate_mod._position_of((3, 'field')) == 3


class TestThroughThePublicApi:
    """The searches are reached from both families, at both sizes."""

    @pytest.mark.parametrize('n', [3, 40])
    @pytest.mark.parametrize('guess', [False, True], ids=['no-guess', 'guess'])
    def test_the_reported_location_names_the_bad_key(self, n, guess):
        from msgspecerror import convert_with_default

        data = {'cells': cell_dict(n, n - 1)}
        _, errors = convert_with_default(data, Dict[str, Dict[str, Cell]], guess_default=guess)

        assert [e.loc for e in errors] == [('cells', f'k{n - 1}', 'v')]

    @pytest.mark.parametrize('n', [3, 40])
    def test_a_bad_key_is_reported_at_its_own_position(self, n):
        from msgspecerror import convert_with_error_detail

        data = {i: 0 for i in range(n)}
        data['bad'] = 0

        _, detail = convert_with_error_detail(data, Dict[int, int])

        assert detail.loc == ('bad',)
        assert detail.error_value == 'bad'
        assert detail.error_value_resolved is True
