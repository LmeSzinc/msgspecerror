from typing import List

from msgspec import NODEFAULT, Struct, field

from msgspecerror.const import ErrorType
from msgspecerror.repair import load_json_with_default


class TestDeadlockPostInit:
    """
    Deadlock detection when ``__post_init__`` rejects the repaired value
    but the model's own default construction (used as the final fallback)
    is acceptable.
    """

    def test_cross_field_constraint_rejected(self):
        """
        A cross-field post_init constraint rejects a single-field repair,
        but the model's own defaults satisfy it. The deadlock should be
        detected immediately and fall back to default construction.
        """

        class Pair(Struct):
            a: int = 1
            b: int = 1

            def __post_init__(self):
                if self.a + self.b != 2:
                    raise ValueError("a + b must equal 2")

        # Field 'a' has a type error, 'b' is valid (= 2)
        data = b'{"a": "bad", "b": 2}'

        # First repair: set a=1 (field default), keep b=2
        # convert: 1+2=3 != 2 -> post_init rejects
        # Second repair: same fix (a=1), same error -> deadlock
        # Fallback: model default Pair() = Pair(1, 1) -> 1+1=2 -> OK
        result, errors = load_json_with_default(data, Pair)

        assert result == Pair(a=1, b=1)
        assert len(errors) >= 2
        assert any(e.type is ErrorType.INPUT_REJECTED for e in errors)

    def test_default_factory_rejected_by_post_init(self):
        """
        A ``default_factory`` result is rejected during repair, but the
        model's own defaults are acceptable.
        """

        class AtLeastOne(Struct):
            items: List[int] = field(default_factory=lambda: [0])

            def __post_init__(self):
                if not self.items:
                    raise ValueError("items must be non-empty")

        # An invalid entry causes the list to be cleared
        data = b'{"items": [1, "bad", 3]}'

        # Repair: pops "bad" from items -> [1, 3] -> post_init passes
        result, errors = load_json_with_default(data, AtLeastOne)

        assert result == AtLeastOne(items=[1, 3])
        assert len(errors) >= 1


class TestInputRejected:
    """Cap on repair iterations to prevent resource exhaustion."""

    def test_too_many_invalid_list_items(self):
        """
        When a list contains more than 100 invalid items, each pop triggers
        a new error at the same index. After 100 repair cycles the cap
        kicks in and an ``INPUT_REJECTED`` error is reported.
        """

        class Many(Struct):
            items: List[int]

        # 101 items as JSON array of strings -> each pop is one iteration
        items_json = ",".join(['"bad"'] * 101)
        data = b'{"items": [' + items_json.encode() + b']}'
        result, errors = load_json_with_default(data, Many)

        assert result is NODEFAULT
        assert any(e.type is ErrorType.INPUT_REJECTED for e in errors)

    def test_too_many_invalid_list_items_default_constructed(self):
        """
        When the model has defaults for all fields and the repair cap is
        exceeded, the result is a valid default-constructed instance.
        """

        class ManyDefaults(Struct):
            items: List[int] = field(default_factory=list)

        # 101 items, all strings -> cap exceeded after 100 pops
        items_json = ",".join(['"bad"'] * 101)
        data = b'{"items": [' + items_json.encode() + b']}'
        result, errors = load_json_with_default(data, ManyDefaults)

        assert result == ManyDefaults()
        assert any(e.type is ErrorType.INPUT_REJECTED for e in errors)
