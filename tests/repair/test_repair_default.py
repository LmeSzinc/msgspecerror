import msgspec
from msgspec import Meta, Struct
from typing_extensions import Annotated

from msgspecerror.repair import load_json_with_default, load_msgpack_with_default


# --- Test Models ---

class Child(Struct):
    """A struct that does not accept an explicit `null` when it is not optional."""
    x: int = 0


class AnnotatedStructDefault(Struct):
    """`Annotated[Child, Meta()]` accepts the default `None` only when the field is absent."""
    child: Annotated[Child, Meta()] = None


class ConstrainedDefault(Struct):
    """The default `0` violates `ge=18`, msgspec only applies it when the field is absent."""
    name: str = "keep"
    v: Annotated[int, Meta(ge=18)] = 0


class ValidDefault(Struct):
    """A failing field with an ordinary default is repaired to that default."""
    count: int = 0
    name: str = "keep"


class TestRepairUnusableFieldDefault:
    """
    A struct default that msgspec rejects when it is explicitly present in the input
    can not be written back, the entry is deleted instead so msgspec applies the
    default itself — the repair must not repeat the same failing fix.
    """

    def test_constrained_default_is_not_written_repeatedly(self):
        data = b'{"name": "hello", "v": "bad"}'
        result, errors = load_json_with_default(data, ConstrainedDefault)

        assert result == ConstrainedDefault(name="hello", v=0)
        assert len(errors) == 1
        assert errors[0].loc == ("v",)

    def test_constrained_default_with_guess_default(self):
        data = b'{"name": "hello", "v": "bad"}'
        result, errors = load_json_with_default(data, ConstrainedDefault, guess_default=True)

        assert result == ConstrainedDefault(name="hello", v=0)
        assert len(errors) == 1
        assert errors[0].loc == ("v",)

    def test_deleting_the_entry_matches_decoding_without_the_field(self):
        """The repair result is the same as decoding the data without the failing field."""
        expected = msgspec.json.decode(b'{"name": "hello"}', type=ConstrainedDefault)

        result, errors = load_json_with_default(b'{"name": "hello", "v": "bad"}', ConstrainedDefault)
        assert result == expected

    def test_annotated_struct_default_is_not_written_repeatedly(self):
        data = b'{"child": 123}'
        result, errors = load_json_with_default(data, AnnotatedStructDefault)

        assert result == AnnotatedStructDefault(child=None)
        assert len(errors) == 1
        assert errors[0].loc == ("child",)

    def test_annotated_struct_default_with_guess_default(self):
        data = b'{"child": 123}'
        result, errors = load_json_with_default(data, AnnotatedStructDefault, guess_default=True)

        assert result == AnnotatedStructDefault(child=None)
        assert len(errors) == 1
        assert errors[0].loc == ("child",)

    def test_msgpack_constrained_default(self):
        data = msgspec.msgpack.encode({"name": "hello", "v": "bad"})
        result, errors = load_msgpack_with_default(data, ConstrainedDefault, guess_default=True)

        assert result == ConstrainedDefault(name="hello", v=0)
        assert len(errors) == 1
        assert errors[0].loc == ("v",)

    def test_valid_default_is_still_written(self):
        """The ordinary repair, writing the field default into the input, is unchanged."""
        data = b'{"count": "bad", "name": "keep"}'
        result, errors = load_json_with_default(data, ValidDefault)

        assert result == ValidDefault(count=0, name="keep")
        assert len(errors) == 1
        assert errors[0].loc == ("count",)

    def test_nested_unusable_default_is_not_written_repeatedly(self):
        class Holder(Struct):
            cfg: ConstrainedDefault = msgspec.field(default_factory=ConstrainedDefault)

        data = b'{"cfg": {"name": "hello", "v": "bad"}}'
        result, errors = load_json_with_default(data, Holder, guess_default=True)

        assert result == Holder(cfg=ConstrainedDefault(name="hello", v=0))
        assert len(errors) == 1
        assert errors[0].loc == ("cfg", "v")
