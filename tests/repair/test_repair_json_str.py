from msgspec import NODEFAULT

from msgspecerror.repair import load_json_with_default
from .test_repair_json import Simple, WithDefaults


class TestLoadJsonWithDefaultStrInput:
    """
    Test suite for `load_json_with_default` with str (not bytes) input.
    """

    def test_str_input_valid(self):
        """Passing a str instead of bytes works for valid JSON."""
        data = '{"a": 1, "b": "hello"}'
        result, errors = load_json_with_default(data, Simple)
        assert result == Simple(a=1, b="hello")
        assert errors == []

    def test_str_input_type_error(self):
        """Passing a str with validation errors works correctly."""
        data = '{"a": "not-int", "b": "hello"}'
        result, errors = load_json_with_default(data, WithDefaults)
        assert result == WithDefaults(a=42, b="hello", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_str_input_malformed(self):
        """Passing a malformed str falls back to default construction."""
        data = '{"a": 1, '
        result, errors = load_json_with_default(data, WithDefaults)
        assert result == WithDefaults(a=42, b="default", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ()

    def test_str_input_with_decoder(self):
        """Passing a str with a JsonDecoder works correctly."""
        import msgspec.json

        decoder = msgspec.json.Decoder(WithDefaults)
        data = '{"a": "not-int", "b": "hello"}'
        result, errors = load_json_with_default(data, decoder)
        assert result == WithDefaults(a=42, b="hello", c=[])
        assert len(errors) == 1
        assert errors[0].loc == ("a",)

    def test_str_input_root_error_unrepairable(self):
        """Passing a str that causes a root-level error with an unrepairable model."""
        data = 'not json at all'
        result, errors = load_json_with_default(data, Simple)
        assert result is NODEFAULT
        assert len(errors) == 1
        assert errors[0].loc == ()
