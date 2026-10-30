"""
Resolve the target type and the validation options of a public API call.

Every public family accepts either a typehint or a `msgspec` decoder. A decoder
carries the rules it validates with, and those rules have to be reused so a
value the caller's decode accepted is not repaired away by a stricter
`convert`. The format of the decoder also decides `str_keys`: json keys are
always strings, msgpack keys are not.
"""

from msgspec.json import Decoder as JsonDecoder
from msgspec.msgpack import Decoder as MsgpackDecoder


def get_decoder_model(decoder_or_model, expected_decoder=None, options=None, caller=None):
    """
    Split a public API argument into its decoder, target type and options.

    The decoder classes can not be subclassed at all, so the type is compared
    exactly. That also avoids reading a `.type` attribute of an unrelated
    object, which is treated as a typehint.

    Args:
        decoder_or_model (type | Decoder): Target type, or a decoder to take
            `.type` / `.strict` / `.dec_hook` / format from
        expected_decoder (type | None): The decoder class a load family needs.
            With a decoder of the other format a TypeError is raised. Defaults
            to None, any decoder is accepted. A non-decoder value is never
            rejected, it is treated as a typehint.
        options (dict | None): Explicit options, they override the ones taken
            from the decoder. Defaults to None, no explicit option.
        caller (str | None): Name of the public function, used in the TypeError
            message. Defaults to None, the message then omits the name.

    Returns:
        tuple[Decoder | None, Any, dict]: (decoder, model, options). `decoder` is
        None when a typehint was passed, `model` is its `.type` otherwise, and
        `options` are the keyword arguments of the equivalent `convert` call.

    Raises:
        TypeError: if `expected_decoder` is given and the argument is a decoder
            of the other format
    """
    cls = type(decoder_or_model)
    if cls is JsonDecoder:
        if expected_decoder is MsgpackDecoder:
            raise TypeError(_type_error(caller, 'msgpack', 'json'))
        resolved = {
            'strict': decoder_or_model.strict,
            'dec_hook': decoder_or_model.dec_hook,
            # A json payload always carries string keys
            'str_keys': True,
        }
        model = decoder_or_model.type
    elif cls is MsgpackDecoder:
        if expected_decoder is JsonDecoder:
            raise TypeError(_type_error(caller, 'json', 'msgpack'))
        # Msgpack keys are not restricted to strings, `str_keys` stays off
        resolved = {
            'strict': decoder_or_model.strict,
            'dec_hook': decoder_or_model.dec_hook,
        }
        model = decoder_or_model.type
    else:
        # A typehint, or an object that only looks like a decoder. The options
        # come from **kwargs, a fresh dict per call, and nothing here mutates
        # them, so they are handed back as they are.
        return None, decoder_or_model, options or {}

    if options:
        resolved.update(options)
    return decoder_or_model, model, resolved


def _type_error(caller, wanted, received):
    """
    Build the TypeError message of a decoder of the wrong format.

    Args:
        caller (str | None): Name of the public function, None to omit it
        wanted (str): Format the caller needs, 'json' or 'msgpack'
        received (str): Format that was passed

    Returns:
        str: The message
    """
    prefix = f'{caller}() ' if caller else ''
    return f'{prefix}requires a msgspec.{wanted}.Decoder, got a msgspec.{received}.Decoder'
