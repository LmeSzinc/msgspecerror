"""
Tests for `get_decoder_model()`, the shared entry of the public API families.
"""
from typing import Any, Dict, List

import msgspec
import pytest
from msgspec.json import Decoder as JsonDecoder
from msgspec.msgpack import Decoder as MsgpackDecoder

from msgspecerror.parse_decoder import get_decoder_model


class Model(msgspec.Struct):
    a: int


def _hook(cls, obj):
    return obj


class TestTypeArgument:
    """A plain typehint is handed back as the model, with no decoder."""

    @pytest.mark.parametrize('model', [Model, int, List[int], Dict[str, Model], None])
    def test_a_typehint(self, model):
        decoder, resolved, options = get_decoder_model(model)

        assert decoder is None
        assert resolved is model
        assert options == {}

    def test_options_are_kept(self):
        """The convert family passes its keyword arguments here."""
        given = {'strict': False}

        decoder, model, options = get_decoder_model(Model, options=given)

        assert decoder is None
        assert model is Model
        assert options == {'strict': False}
        # `**kwargs` builds a new dict per call and nothing here mutates it, so
        # there is no copy to make
        assert options is given

    def test_no_options_gives_an_empty_dict(self):
        assert get_decoder_model(Model)[2] == {}

    def test_an_unrelated_object_is_a_typehint(self):
        """Only an exact decoder type is read, no `.type` attribute is touched."""

        class Fake:
            type = Model

        decoder, model, options = get_decoder_model(Fake())

        assert decoder is None
        assert isinstance(model, Fake)
        assert options == {}


class TestJsonDecoder:
    """A json decoder carries its rules, and its keys are always strings."""

    def test_the_decoder_and_its_type(self):
        decoder = JsonDecoder(Model)

        resolved, model, options = get_decoder_model(decoder)

        assert resolved is decoder
        assert model is Model
        assert options == {'strict': True, 'dec_hook': None, 'str_keys': True}

    def test_the_decoder_rules_are_kept(self):
        decoder = JsonDecoder(Model, strict=False, dec_hook=_hook)

        _, _, options = get_decoder_model(decoder)

        assert options['strict'] is False
        assert options['dec_hook'] is _hook

    def test_an_explicit_option_wins(self):
        decoder = JsonDecoder(Model, strict=False)

        _, _, options = get_decoder_model(decoder, options={'strict': True, 'str_keys': False})

        assert options['strict'] is True
        # a json payload always carries string keys, but an explicit call decides
        assert options['str_keys'] is False


class TestMsgpackDecoder:
    """Msgpack keys are not restricted to strings, so `str_keys` stays off."""

    def test_no_str_keys(self):
        decoder = MsgpackDecoder(Model)

        resolved, model, options = get_decoder_model(decoder)

        assert resolved is decoder
        assert model is Model
        assert options == {'strict': True, 'dec_hook': None}

    def test_the_decoder_rules_are_kept(self):
        decoder = MsgpackDecoder(Model, strict=False, dec_hook=_hook)

        _, _, options = get_decoder_model(decoder)

        assert options['strict'] is False
        assert options['dec_hook'] is _hook


class TestExpectedDecoder:
    """A load family rejects the decoder of the other format."""

    @pytest.mark.parametrize('expected, decoder_cls', [
        (JsonDecoder, JsonDecoder),
        (MsgpackDecoder, MsgpackDecoder),
    ])
    def test_the_expected_decoder_is_accepted(self, expected, decoder_cls):
        decoder = decoder_cls(Model)

        resolved, model, _ = get_decoder_model(decoder, expected, caller='load_x')

        assert resolved is decoder
        assert model is Model

    def test_the_other_decoder_is_rejected(self):
        with pytest.raises(
                TypeError,
                match=r'load_json_with_default\(\) requires a msgspec\.json\.Decoder, '
                      r'got a msgspec\.msgpack\.Decoder',
        ):
            get_decoder_model(
                MsgpackDecoder(Model), JsonDecoder, caller='load_json_with_default')

    def test_the_other_decoder_is_rejected_the_other_way(self):
        with pytest.raises(
                TypeError,
                match=r'load_msgpack_with_default\(\) requires a msgspec\.msgpack\.Decoder, '
                      r'got a msgspec\.json\.Decoder',
        ):
            get_decoder_model(
                JsonDecoder(Model), MsgpackDecoder, caller='load_msgpack_with_default')

    def test_the_message_without_a_caller(self):
        with pytest.raises(TypeError, match=r'^requires a msgspec\.json\.Decoder'):
            get_decoder_model(MsgpackDecoder(Model), JsonDecoder)

    @pytest.mark.parametrize('expected', [JsonDecoder, MsgpackDecoder])
    def test_a_typehint_is_never_rejected(self, expected):
        """An expected decoder narrows decoders only, a model is always fine."""
        decoder, model, options = get_decoder_model(Model, expected, caller='load_x')

        assert decoder is None
        assert model is Model
        assert options == {}

    def test_a_duck_typed_decoder_is_a_typehint(self):
        """
        The decoder classes can not be subclassed at all, so a look-alike object
        is a typehint, not a decoder: nothing reads its attributes.
        """

        class LookAlike:
            type = Model
            strict = False
            dec_hook = _hook

        look_alike = LookAlike()

        decoder, model, options = get_decoder_model(look_alike, JsonDecoder, caller='load_x')

        assert decoder is None
        assert model is look_alike
        assert options == {}


class TestOptionsAreNotShared:
    """The returned dict is built per call, a caller can not leak into another."""

    def test_two_calls_do_not_share_state(self):
        first = get_decoder_model(JsonDecoder(Model))[2]
        second = get_decoder_model(JsonDecoder(Model))[2]

        assert first == second
        assert first is not second

        first['strict'] = False
        assert second['strict'] is True

    def test_the_given_options_are_not_modified(self):
        given: Dict[str, Any] = {'strict': False}

        get_decoder_model(JsonDecoder(Model), options=given)

        assert given == {'strict': False}
