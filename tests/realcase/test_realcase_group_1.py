"""
Real-case tests for Group 1: Type Mismatch & Unexpected Token.

Every test triggers a real msgspec error by decoding malformed data,
then validates that parse_msgspec_error correctly classifies it.
"""
import dataclasses
from typing import Dict, List, Optional, Union

import msgspec
import pytest
from typing_extensions import TypedDict

from msgspecerror import parse_msgspec_error
from msgspecerror.const import MSGSPEC_0_22_PLUS, ErrorType


class TestTypeMismatchReal:
    """TYPE_MISMATCH — triggered by real type mismatches."""

    def test_int_got_str(self):
        """Expected `int`, got `str` - at `$.age`"""

        class Model(msgspec.Struct):
            age: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"age": "not_an_int"}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("age",)
        assert err.ctx.expected == "int"
        assert err.ctx.got == "str"

    def test_str_got_int(self):
        """Expected `str`, got `int` - at `$.name`"""

        class Model(msgspec.Struct):
            name: str

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": 42}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("name",)
        assert err.ctx.expected == "str"
        assert err.ctx.got == "int"

    def test_bool_got_str(self):
        """Expected `bool`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"hello"', type=bool)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "bool"
        assert err.ctx.got == "str"

    def test_int_got_null(self):
        """Expected `int`, got `null`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'null', type=int)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "int"
        assert err.ctx.got == "null"

    def test_array_got_str(self):
        """Expected `array`, got `str`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"not_array"', type=List[int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "array"
        assert err.ctx.got == "str"

    def test_object_got_int(self):
        """Expected `object`, got `int`"""

        class Model(msgspec.Struct):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "object"
        assert err.ctx.got == "int"

    def test_float_got_str_deep(self):
        """Expected `float`, got `str` - at `$.inner.value`"""

        class Inner(msgspec.Struct):
            value: float

        class Outer(msgspec.Struct):
            inner: Inner

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"inner": {"value": "not_float"}}', type=Outer)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("inner", "value")
        assert err.ctx.expected == "float"
        assert err.ctx.got == "str"

    def test_list_index_path(self):
        """Expected `int`, got `str` - at `$.items[1]`"""

        class Model(msgspec.Struct):
            items: List[int]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"items": [1, "bad", 3]}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ("items", 1)
        assert err.ctx.expected == "int"
        assert err.ctx.got == "str"

    def test_bytes_got_int(self):
        """Expected `bytes`, got `int`"""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'42', type=bytes)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.expected == "bytes"
        assert err.ctx.got == "int"


class TestTokenTypeMismatchReal:
    """TOKEN_TYPE_MISMATCH — triggered by tag value / token type mismatches."""

    # ==================================================================
    # Trigger 1: Tag Value Mismatch (JSON)
    # ==================================================================

    def test_tag_field_str_got_int(self):
        """Expected `str` - at `$.type`"""

        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Union[Cat, Dog]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": 1}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("type",)
        assert err.ctx.expected == "str"

    def test_tag_field_int_got_str(self):
        """Invalid value 'xyz' - at `$.type`

        Tag value 'xyz' doesn't match any known type but is a string,
        so it goes through tag lookup first and produces INVALID_TAG_VALUE.
        """

        class Cat(msgspec.Struct, tag=True):
            name: str

        class Dog(msgspec.Struct, tag=True):
            name: str

        Animal = Union[Cat, Dog]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"name": "fluffy", "type": "xyz"}', type=Animal)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.INVALID_TAG_VALUE

    def test_tag_field_str_in_int_union(self):
        """Expected `str` - at `$.type`

        tag=True -> tag value is str (class name). Pass a number to
        trigger TOKEN_TYPE_MISMATCH.
        """

        class A(msgspec.Struct, tag=True):
            pass

        class B(msgspec.Struct, tag=True):
            pass

        AB = Union[A, B]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"type": 999}', type=AB)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("type",)
        assert err.ctx.expected == "str"

    # ==================================================================
    # Trigger 2: Map Key Mismatch (convert)
    #   convert_is_str_key: non-string dict key in msgspec.convert()
    #   when target type is Struct / TypedDict / dataclass
    # ==================================================================

    def test_convert_non_str_key_struct(self):
        """Expected `str` - at `key` in `$`

        msgspec.convert() with a Struct target rejects non-string keys
        because struct field names are always strings.
        """

        class Model(msgspec.Struct):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.convert({1: 10}, Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == ("...key",)
        assert err.ctx.expected == "str"

    # ==================================================================
    # Trigger 3: Token Type Mismatch (JSON - array-like tag)
    #   json_decode_cstr: array first element expected str tag, got int/bool/null
    #   Error message: "Expected `str` - at `$[0]`"
    # ==================================================================

    def test_array_like_str_tag_got_int(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag (class name) as the
        first element, but finds an int instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[123, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"

    def test_array_like_str_tag_got_bool(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag as the first element,
        but finds a JSON boolean instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[true, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"

    def test_array_like_str_tag_got_null(self):
        """Expected `str` - at `$[0]`

        Array-like tagged struct expects a str tag as the first element,
        but finds a JSON null instead.
        """

        class Tagged(msgspec.Struct, tag=True, array_like=True):
            x: int

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'[null, 42]', type=Tagged)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TOKEN_TYPE_MISMATCH
        assert err.loc == (0,)
        assert err.ctx.expected == "str"


class TestRawRejectionReal:
    """
    TYPE_MISMATCH that comes from a `msgspec.Raw` target.

    A raw field keeps the payload bytes, so the decoder accepts it while
    `msgspec.convert` only takes a `Raw` instance. The message is the only
    signal `parse_msgspec_error()` has, and its type name moves with the
    msgspec version: `raw` from 0.22 on, `any` before it (plus an empty name
    for a field that carries metadata), so every real shape is pinned here.
    """

    class Event(msgspec.Struct):
        """A webhook-like event whose sub-document is kept as bytes."""
        kind: str
        payload: msgspec.Raw

    DOC = b'{"kind": "click", "payload": {"x": 1}}'

    # The type name msgspec gives a `Raw` target, by version
    RAW = 'raw' if MSGSPEC_0_22_PLUS else 'any'

    def test_convert_of_a_decoded_raw_field(self):
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.convert(msgspec.json.decode(self.DOC), type=self.Event)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ('payload',)
        assert err.ctx.expected == self.RAW
        assert err.ctx.may_raw_rejection is True

    def test_the_same_payload_decodes(self):
        """The error above is the re-validation, the payload itself is fine."""
        event = msgspec.json.decode(self.DOC, type=self.Event)
        assert event.kind == 'click'
        assert msgspec.json.decode(event.payload) == {'x': 1}

    def test_a_default_factory_hides_the_type_name(self):
        """A field carrying metadata has no name before 0.22, `raw` from then on."""

        @dataclasses.dataclass
        class Envelope:
            payload: msgspec.Raw = dataclasses.field(default_factory=lambda: msgspec.Raw(b''))

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.convert({'payload': {'x': 1}}, type=Envelope)
        err = parse_msgspec_error(exc_info.value)
        assert err.ctx.expected == ('raw' if MSGSPEC_0_22_PLUS else None)
        assert err.ctx.may_raw_rejection is True

    def test_a_typed_dict_raw_field(self):
        """A page of records whose attachment is kept as bytes."""

        class Page(TypedDict, total=False):
            attachment: msgspec.Raw
            title: str

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.convert({'attachment': {'x': 1}, 'title': 'hi'}, type=Page)
        err = parse_msgspec_error(exc_info.value)
        assert err.loc == ('attachment',)
        assert err.ctx.expected == self.RAW
        assert err.ctx.may_raw_rejection is True

    def test_a_user_hook_message_can_mimic_the_shape(self):
        """Known limit: msgspec does not mark a wrapped user-code error."""

        class Token:
            def __init__(self, value):
                self.value = value

        def dec_hook(type, obj):
            raise ValueError(f'Expected `{self.RAW}`, got `str`')

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'"abc"', type=Token, dec_hook=dec_hook)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.ctx.may_raw_rejection is True

    def test_a_plain_type_mismatch_is_not_raw_shaped(self):
        """The common case stays cheap: no raw candidate at all."""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"kind": 1, "payload": {}}', type=self.Event)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ('kind',)
        assert err.ctx.may_raw_rejection is False


class TestRawShapeFalsePositivesReal:
    """
    Messages that look like a raw rejection but are real errors.

    The decoder rejects these payloads by itself, so no `convert` artifact is
    involved: the message shape is not enough and `loc` / the model have to
    disagree with it.
    """

    RAW = 'raw' if MSGSPEC_0_22_PLUS else 'any'

    def test_a_raw_dict_key_is_raw_shaped_but_not_a_raw_field(self):
        """`dict[Raw, int]` reports the key, and a key can not hold bytes."""
        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"a": 1}', type=Dict[msgspec.Raw, int])
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ('...key',)
        assert err.ctx.expected == self.RAW
        # shaped like a raw field, but the location is a key
        assert err.ctx.may_raw_rejection is True

    def test_a_null_check_on_optional_raw_is_a_real_error(self):
        """`Optional[Raw]` means `null`, the decoder rejects the object as well."""

        class Model(msgspec.Struct):
            payload: Optional[msgspec.Raw]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"payload": 1}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ('payload',)
        assert err.ctx.expected == 'null'
        assert err.ctx.may_raw_rejection is False

    def test_a_union_member_error_is_a_real_error(self):
        """`Union[Raw, int]` names the member that rejected the value."""

        class Model(msgspec.Struct):
            v: Union[msgspec.Raw, int]

        with pytest.raises(msgspec.ValidationError) as exc_info:
            msgspec.json.decode(b'{"v": {"a": 1}}', type=Model)
        err = parse_msgspec_error(exc_info.value)
        assert err.type == ErrorType.TYPE_MISMATCH
        assert err.loc == ('v',)
        assert err.ctx.expected == 'int'
        assert err.ctx.may_raw_rejection is False
