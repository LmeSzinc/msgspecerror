from enum import Enum
from typing import Dict, List

from msgspec import NODEFAULT, Struct

import msgspecerror.const
from msgspecerror.const import ErrorType
from msgspecerror.repair import _pop_last_collection, load_json_with_default


# --- Test Models ---

class Inner(Struct):
    """A struct that cannot be default-constructed."""
    x: int


class Mid(Struct):
    """A struct whose field is a dict of Inner."""
    m: Dict[str, Inner] = {}


class RequiredMid(Struct):
    """A struct with a required Inner field."""
    s: Inner


class Mode(Enum):
    """An enum, enum values are never guessed."""
    A = 1
    B = 2


class ModeMid(Struct):
    """A struct with an unguessable enum field."""
    mode: Mode


class DictOfInner(Struct):
    """A dict of Inner, the entry is deleted when Inner can't be repaired."""
    d: Dict[str, Inner] = {}


class ListOfInner(Struct):
    """A list of Inner, the item is deleted when Inner can't be repaired."""
    items: List[Inner] = []


class DictOfMid(Struct):
    """A dict of Mid, mid is a struct with a dict of Inner."""
    d: Dict[str, Mid] = {}


class DictOfRequiredMid(Struct):
    """A dict of RequiredMid, RequiredMid can't be default-constructed."""
    d: Dict[str, RequiredMid] = {}


class DictOfModeMid(Struct):
    """A dict of ModeMid, the enum field can't be guessed."""
    d: Dict[str, ModeMid] = {}


class DictOfListOfInner(Struct):
    """A dict of lists of Inner."""
    d: Dict[str, List[Inner]] = {}


class ListOfDictOfInner(Struct):
    """A list of dicts of Inner."""
    items: List[Dict[str, Inner]] = []


class DictOfDictOfInner(Struct):
    """A dict of dicts of Inner."""
    d: Dict[str, Dict[str, Inner]] = {}


class TestRepairCollection:
    """
    A failing value inside a dict entry or list item deletes that entry / item, as the
    minimal repair, and keeps the rest of the data untouched.
    """

    def test_dict_entry_with_missing_field_is_deleted(self):
        """A dict entry that misses a required field is deleted, the valid entries are kept."""
        data = b'{"d": {"ok": {"x": 1}, "bad": {}}}'
        result, errors = load_json_with_default(data, DictOfInner)

        assert result == DictOfInner(d={"ok": Inner(x=1)})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "x")

    def test_list_item_with_missing_field_is_deleted(self):
        """A list item that misses a required field is deleted, the valid items are kept."""
        data = b'{"items": [{"x": 1}, {}]}'
        result, errors = load_json_with_default(data, ListOfInner)

        assert result == ListOfInner(items=[Inner(x=1)])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 1, "x")

    def test_entry_is_guessed_when_guessing_is_enabled(self):
        """With guess_default=True, the field is guessed instead of deleting the entry."""
        data = b'{"d": {"ok": {"x": 1}, "bad": {}}}'
        result, errors = load_json_with_default(data, DictOfInner, guess_default=True)

        assert result == DictOfInner(d={"ok": Inner(x=1), "bad": Inner(x=0)})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "x")

    def test_unguessable_nested_value_is_deleted_when_guessing(self):
        """A nested value that can't be guessed (enum) deletes the entry even when guessing."""
        data = b'{"d": {"ok": {"mode": 1}, "bad": {"mode": 3}}}'
        result, errors = load_json_with_default(data, DictOfModeMid, guess_default=True)

        assert result == DictOfModeMid(d={"ok": ModeMid(mode=Mode.A)})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "mode")

    # --- Complex Nesting ---

    def test_dict_value_is_a_list_of_structs(self):
        """Only the failing list item is deleted, the dict entry is kept."""
        data = b'{"d": {"k": [{"x": 1}, {}]}}'
        result, errors = load_json_with_default(data, DictOfListOfInner)

        assert result == DictOfListOfInner(d={"k": [Inner(x=1)]})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "k", 1, "x")

    def test_list_item_is_a_dict_of_structs(self):
        """Only the failing dict entry is deleted, the list item is kept."""
        data = b'{"items": [{"ok": {"x": 1}, "bad": {}}]}'
        result, errors = load_json_with_default(data, ListOfDictOfInner)

        assert result == ListOfDictOfInner(items=[{"ok": Inner(x=1)}])
        assert len(errors) == 1
        assert errors[0].loc == ("items", 0, "bad", "x")

    def test_dict_value_is_a_dict_of_structs(self):
        """Only the innermost failing entry is deleted, all outer entries are kept."""
        data = b'{"d": {"ok": {"k": {"x": 1}}, "bad": {"k": {}}}}'
        result, errors = load_json_with_default(data, DictOfDictOfInner)

        assert result == DictOfDictOfInner(d={"ok": {"k": Inner(x=1)}, "bad": {}})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "k", "x")

    def test_mixed_dict_struct_dict_nesting(self):
        """dict -> struct -> dict -> struct, only the innermost entry is deleted."""
        data = b'{"d": {"ok": {"m": {"k": {"x": 1}}}, "bad": {"m": {"k": {}}}}}'
        result, errors = load_json_with_default(data, DictOfMid)

        assert result == DictOfMid(d={"ok": Mid(m={"k": Inner(x=1)}), "bad": Mid(m={})})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "m", "k", "x")

    def test_entry_is_deleted_when_the_struct_field_fails(self):
        """The dict entry is deleted when its struct field can't be repaired."""
        data = b'{"d": {"ok": {"s": {"x": 1}}, "bad": {"s": {}}}}'
        result, errors = load_json_with_default(data, DictOfRequiredMid)

        assert result == DictOfRequiredMid(d={"ok": RequiredMid(s=Inner(x=1))})
        assert len(errors) == 1
        assert errors[0].loc == ("d", "bad", "s", "x")

    def test_multiple_failing_entries_are_deleted(self):
        """Every failing entry is deleted, the valid entries are kept."""
        data = b'{"d": {"a": {}, "ok": {"x": 1}, "b": {}}}'
        result, errors = load_json_with_default(data, DictOfInner)

        assert result == DictOfInner(d={"ok": Inner(x=1)})
        assert len(errors) == 2
        assert [e.loc for e in errors] == [("d", "a", "x"), ("d", "b", "x")]

    def test_no_container_above_leaves_repair_failed(self):
        """A failing struct field with no enclosing dict entry / list item can't be repaired."""
        data = b'{"s": {}}'
        result, errors = load_json_with_default(data, RequiredMid)

        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ("s", "x")

    # --- The pop of the cached collection ---

    def test_pop_removes_the_entry(self):
        """The dict entry / list item found during the walk is popped."""
        data = {"bad": 1, "ok": 2}
        assert _pop_last_collection(data, "bad") is True
        assert data == {"ok": 2}

        data = [1, 2, 3]
        assert _pop_last_collection(data, 1) is True
        assert data == [1, 3]

    def test_pop_missing_key_is_treated_as_failed_repair(self):
        """A stale key in the cached dict must not raise, it means the repair failed."""
        data = {}
        assert _pop_last_collection(data, "missing") is False
        assert data == {}

    def test_pop_out_of_range_index_is_treated_as_failed_repair(self):
        """A stale index in the cached list must not raise, it means the repair failed."""
        data = [1, 2]
        assert _pop_last_collection(data, 5) is False
        assert data == [1, 2]

    def test_pop_without_cached_collection_is_treated_as_failed_repair(self):
        """Nothing was stepped into, there is nothing to pop."""
        assert _pop_last_collection(None, None) is False

    # --- Maximum repair ---

    def test_too_many_failing_dict_entries(self):
        """
        Deleting dict entries whose nested repair fails is also capped by
        ``MAXIMUM_REPAIR``, one deletion per repair cycle.
        """

        class Item(Struct):
            x: int

        class Many(Struct):
            d: Dict[str, Item]

        # 101 entries missing the required field `x` -> cap hit after 100 deletions
        entries = ", ".join(f'"{i}": {{}}' for i in range(101))
        data = ('{"d": {' + entries + '}}').encode()
        result, errors = load_json_with_default(data, Many)

        assert result is NODEFAULT
        missing_fields = [e for e in errors if e.type is ErrorType.MISSING_FIELD]
        assert len(missing_fields) == 100, (
            f"expected 100 MISSING_FIELD (one per deletion), got {len(missing_fields)}"
        )
        assert any(e.type is ErrorType.INPUT_REJECTED for e in errors)
