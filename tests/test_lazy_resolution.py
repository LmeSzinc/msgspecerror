"""
Tests for the lazy argument resolution of the load families.

A load family only picks the decoder before it decodes. The model and the
`convert` options describe how a failed entry has to be judged, and nothing on
the success path reads them, so they are resolved after the decode has failed.
These tests pin that contract: a payload that decodes must not pay for the
repair options, and an error must still resolve them exactly once.

`load_*_with_error` is the strictest case, it never resolves them: its failure
is handed back as msgspec reported it, with no entry to judge.
"""
import ast
import inspect
from collections import namedtuple

import msgspec
import pytest
from msgspec import Struct

import msgspecerror.repair as repair_mod
import msgspecerror.with_error as with_error_mod
from msgspecerror import (
    load_json_with_default, load_json_with_error, load_json_with_error_detail, load_msgpack_with_default,
    load_msgpack_with_error, load_msgpack_with_error_detail
)
from msgspecerror.const import ErrorType
from msgspecerror.parse_decoder import get_decoder_model


class Point(Struct):
    x: int
    y: int


def make_json_decoder(strict=True):
    """
    Build a json decoder for the benchmark model.

    Args:
        strict (bool): The `strict` flag of the decoder. Defaults to True.

    Returns:
        msgspec.json.Decoder: The decoder
    """
    return msgspec.json.Decoder(Point, strict=strict)


def make_msgpack_decoder(strict=True):
    """
    Build a msgpack decoder for the benchmark model.

    Args:
        strict (bool): The `strict` flag of the decoder. Defaults to True.

    Returns:
        msgspec.msgpack.Decoder: The decoder
    """
    return msgspec.msgpack.Decoder(Point, strict=strict)


JSON_OK = b'{"x": 1, "y": 2}'
JSON_BAD = b'{"x": "bad", "y": 2}'
MSGPACK_OK = msgspec.msgpack.encode({'x': 1, 'y': 2})
MSGPACK_BAD = msgspec.msgpack.encode({'x': 'bad', 'y': 2})

# `on_error` is how many times a failed payload is expected to resolve the
# arguments: a `*_with_error` call has no entry to judge, so none.
LoadCase = namedtuple('LoadCase', 'label function ok_payload bad_payload make_decoder on_error')

LOAD_CASES = [
    LoadCase('with_error_json', load_json_with_error, JSON_OK, JSON_BAD, make_json_decoder, 0),
    LoadCase('with_error_msgpack', load_msgpack_with_error, MSGPACK_OK, MSGPACK_BAD, make_msgpack_decoder, 0),
    LoadCase('detail_json', load_json_with_error_detail, JSON_OK, JSON_BAD, make_json_decoder, 1),
    LoadCase('detail_msgpack', load_msgpack_with_error_detail, MSGPACK_OK, MSGPACK_BAD, make_msgpack_decoder, 1),
    LoadCase('default_json', load_json_with_default, JSON_OK, JSON_BAD, make_json_decoder, 1),
    LoadCase('default_msgpack', load_msgpack_with_default, MSGPACK_OK, MSGPACK_BAD, make_msgpack_decoder, 1),
]

CASE_IDS = [case.label for case in LOAD_CASES]


class ResolveCounter:
    """Count the calls of the `get_decoder_model` of a module, then delegate."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return get_decoder_model(*args, **kwargs)


@pytest.fixture
def resolve_calls(monkeypatch):
    """
    Patch the resolution of both load modules, and count the calls.

    Args:
        monkeypatch: pytest monkeypatch fixture

    Returns:
        ResolveCounter: The counter, its `calls` attribute is the call count
    """
    counter = ResolveCounter()
    monkeypatch.setattr(with_error_mod, 'get_decoder_model', counter)
    monkeypatch.setattr(repair_mod, 'get_decoder_model', counter)
    return counter


@pytest.fixture(params=[False, True], ids=['typehint', 'decoder'])
def as_decoder(request):
    """
    Whether the case is exercised with a decoder instead of the typehint.

    Args:
        request: pytest request, its param is the flag

    Returns:
        bool: True for a decoder argument, False for the typehint
    """
    return request.param


class TestHappyPath:
    """A payload that decodes never builds the `convert` options."""

    @pytest.mark.parametrize('case', LOAD_CASES, ids=CASE_IDS)
    def test_the_arguments_are_not_resolved(self, resolve_calls, case, as_decoder):
        argument = case.make_decoder() if as_decoder else Point

        obj, _ = case.function(case.ok_payload, argument)

        assert obj == Point(x=1, y=2)
        assert resolve_calls.calls == 0


class TestErrorPath:
    """A payload that fails resolves the arguments itself, once."""

    @pytest.mark.parametrize('case', LOAD_CASES, ids=CASE_IDS)
    def test_the_arguments_are_resolved(self, resolve_calls, case, as_decoder):
        argument = case.make_decoder() if as_decoder else Point

        case.function(case.bad_payload, argument)

        assert resolve_calls.calls == case.on_error


class TestTheFailedPayloadIsStillJudged:
    """The deferred resolution produces the same result as resolving upfront."""

    def test_the_detail_locates_the_entry(self, as_decoder):
        argument = make_json_decoder() if as_decoder else Point

        obj, detail = load_json_with_error_detail(JSON_BAD, argument)

        assert obj is msgspec.NODEFAULT
        assert detail.loc == ('x',)
        assert detail.error_value == 'bad'
        assert detail.loc_resolved is True

    def test_the_repair_locates_the_entry(self):
        result, errors = load_json_with_default(JSON_BAD, Point, guess_default=True)

        assert result == Point(x=0, y=2)
        assert [error.loc for error in errors] == [('x',)]

    def test_a_coercing_decoder_decodes_the_payload(self):
        """A decoder that coerces is used as it is, nothing is resolved to fight it."""
        decoder = make_json_decoder(strict=False)

        result, errors = load_json_with_default(b'{"x": "1", "y": 2}', decoder)

        assert result == Point(x=1, y=2)
        assert errors == []

    @pytest.mark.parametrize('load, data, make_decoder', [
        (load_json_with_default, b'{"x": "1", "y": "bad"}', make_json_decoder),
        (load_msgpack_with_default, msgspec.msgpack.encode({'x': '1', 'y': 'bad'}), make_msgpack_decoder),
    ], ids=['json', 'msgpack'])
    def test_the_repair_judges_with_the_decoder_rules(self, load, data, make_decoder):
        """A value the decoder accepted must not be repaired away as invalid."""
        result, errors = load(data, make_decoder(strict=False), guess_default=True)

        assert result == Point(x=1, y=0)
        assert [error.loc for error in errors] == [('y',)]


class TestModelIsBound:
    """
    Every decoder branch binds `model`, not only the typehint branch.

    The `type=` call below the class check is short-circuited for a decoder, so
    an unbound `model` would never be read today. It is still assigned for both
    kinds of argument: the call site must not depend on the branch above it for
    the name to exist. The invariant can only be checked in the source, a
    decoder never reaches the branch that reads it.
    """

    FUNCTIONS = [
        (with_error_mod, 'load_json_with_error', 'JsonDecoder'),
        (with_error_mod, 'load_msgpack_with_error', 'MsgpackDecoder'),
        (with_error_mod, 'load_json_with_error_detail', 'JsonDecoder'),
        (with_error_mod, 'load_msgpack_with_error_detail', 'MsgpackDecoder'),
        (repair_mod, 'load_json_with_default', 'JsonDecoder'),
        (repair_mod, 'load_msgpack_with_default', 'MsgpackDecoder'),
    ]

    @staticmethod
    def _assigned_names(body):
        """
        Names assigned directly in a branch body.

        Args:
            body (list): Statements of the branch

        Returns:
            set[str]: Target names of its assignments
        """
        return {
            target.id
            for statement in body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }

    @staticmethod
    def _class_check(node):
        """
        Read an `if cls is <Name>:` statement.

        Args:
            node: Candidate AST node

        Returns:
            str | None: The compared class name, None when `node` is not a
                class check
        """
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            return None
        test = node.test
        if not isinstance(test.left, ast.Name) or test.left.id != 'cls':
            return None
        comparator = test.comparators[0]
        if not isinstance(comparator, ast.Name):
            return None
        return comparator.id

    @classmethod
    def _class_check_chain(cls, function):
        """
        Split the `if cls is A: ... elif cls is B: ... else: ...` chain.

        Args:
            function: The load function to inspect

        Returns:
            tuple[dict[str, list], list]: Branch bodies by class name, and the
                statements of the closing `else`
        """
        tree = ast.parse(inspect.getsource(function))
        head = None
        for node in ast.walk(tree):
            if cls._class_check(node) is not None:
                head = node
                break
        if head is None:
            return {}, []

        branches = {}
        node = head
        while True:
            name = cls._class_check(node)
            if name is None:
                # The closing `else` is a plain body of statements
                return branches, node.body if isinstance(node, ast.If) else []
            branches[name] = node.body
            if len(node.orelse) == 1 and cls._class_check(node.orelse[0]) is not None:
                node = node.orelse[0]
            else:
                return branches, node.orelse

    @pytest.mark.parametrize('module, name, decoder_name', FUNCTIONS,
                             ids=[case[1] for case in FUNCTIONS])
    def test_the_decoder_branch_binds_the_model(self, module, name, decoder_name):
        branches, _ = self._class_check_chain(getattr(module, name))

        assert decoder_name in branches, f'{name}() has no `if cls is {decoder_name}` branch'
        assert 'model' in self._assigned_names(branches[decoder_name]), \
            f'{name}() leaves model unbound for a decoder'

    @pytest.mark.parametrize('module, name, decoder_name', FUNCTIONS,
                             ids=[case[1] for case in FUNCTIONS])
    def test_the_typehint_branch_binds_the_model(self, module, name, decoder_name):
        branches, typehint_body = self._class_check_chain(getattr(module, name))

        assert decoder_name in branches, f'{name}() has no class check'
        assert 'model' in self._assigned_names(typehint_body), f'{name}() leaves model unbound for a typehint'


class TestEveryErrorBranch:
    """
    Every error branch has to work for both kinds of argument.

    The model is only bound when a typehint was passed, a decoder decodes the
    payload itself and the error branches resolve what they judge with. A
    branch that reaches for an unbound name would raise UnboundLocalError here
    instead of a reported error.
    """

    JSON_PAYLOADS = [
        ('validation', b'{"x": "bad", "y": 2}'),
        ('malformed', b'{"x": 1'),
        ('bad_utf8', b'{"x": 1}\xff'),
        ('lone_surrogate', '{"x": 1}\ud800'),
    ]

    MSGPACK_PAYLOADS = [
        ('validation', msgspec.msgpack.encode({'x': 'bad', 'y': 2})),
        ('malformed', b'\xc1'),
    ]

    @pytest.mark.parametrize('label, payload', JSON_PAYLOADS, ids=[case[0] for case in JSON_PAYLOADS])
    @pytest.mark.parametrize('load', [load_json_with_error, load_json_with_error_detail, load_json_with_default],
                             ids=['with_error', 'detail', 'default'])
    def test_a_json_failure_is_reported(self, load, label, payload, as_decoder):
        argument = make_json_decoder() if as_decoder else Point

        obj, reported = load(payload, argument)

        assert obj is msgspec.NODEFAULT
        assert reported

    @pytest.mark.parametrize('label, payload', MSGPACK_PAYLOADS, ids=[case[0] for case in MSGPACK_PAYLOADS])
    @pytest.mark.parametrize(
        'load', [load_msgpack_with_error, load_msgpack_with_error_detail, load_msgpack_with_default],
        ids=['with_error', 'detail', 'default'])
    def test_a_msgpack_failure_is_reported(self, load, label, payload, as_decoder):
        argument = make_msgpack_decoder() if as_decoder else Point

        obj, reported = load(payload, argument)

        assert obj is msgspec.NODEFAULT
        assert reported


class Text(Struct):
    """A struct with a str field, so a repaired unicode string has somewhere to go."""
    a: int = 0
    b: str = ''


class RawText(Struct):
    """A struct whose raw field can hide invalid utf-8."""
    payload: msgspec.Raw
    n: int


# `a` is an int and `b` holds two bytes that are not utf-8
UNICODE_BAD_MSGPACK = b'\x82\xa1a\x01\xa1b\xa3x\xffy'
UNICODE_BAD_JSON = b'{"a": 1, "b": "x\xffy"}'
# The raw field holds invalid utf-8, and `n` is the real error
RAW_UTF8_JSON = b'{"payload": {"x": "\xff\xfe"}, "n": "bad"}'


class TestTheBranchThatReadsTheOptions:
    """Only the path that judges an object resolves the `convert` options.

    A unicode repair rewrites the bytes and retries the decode, and a root
    error answers with a default for the whole model: neither judges an entry,
    so neither reads the options, and resolving them there is work thrown away.
    """

    def test_a_msgpack_unicode_repair_resolves_nothing(self, resolve_calls):
        result, errors = load_msgpack_with_default(UNICODE_BAD_MSGPACK, Text)

        assert result == Text(a=1, b='x\ufffdy')
        assert resolve_calls.calls == 0

    def test_a_msgpack_strict_unicode_error_resolves_nothing(self, resolve_calls):
        result, errors = load_msgpack_with_default(UNICODE_BAD_MSGPACK, Text, utf8_error='strict')

        assert result == Text()
        assert [error.type for error in errors] == [ErrorType.UNICODE_DECODE_ERROR]
        assert resolve_calls.calls == 0

    def test_a_json_unicode_repair_resolves_nothing(self, resolve_calls):
        result, errors = load_json_with_default(UNICODE_BAD_JSON, Text)

        assert result == Text(a=1, b='x\ufffdy')
        assert resolve_calls.calls == 0

    def test_a_raw_field_behind_a_unicode_error_resolves_once(self, resolve_calls):
        """The retry that rewrites the bytes does not resolve, the repair does."""
        result, errors = load_json_with_default(RAW_UTF8_JSON, RawText, guess_default=True)

        assert result.n == 0
        assert resolve_calls.calls == 1

    def test_a_decoder_still_hands_its_rules_to_the_repair(self, resolve_calls):
        """Resolving on the failing branch keeps the decoder's rules."""
        argument = msgspec.json.Decoder(Text, strict=False)

        result, errors = load_json_with_default(b'{"a": "1", "b": "x"}', argument)

        # `strict=False` coerces the numeric string, so nothing needed repairing
        assert result == Text(a=1, b='x')
        assert errors == []
        assert resolve_calls.calls == 0


class TestConvertWithErrorDetail:
    """`convert_with_error_detail` already holds the model and the options."""

    def test_a_failure_resolves_once(self, resolve_calls):
        from msgspecerror import convert_with_error_detail

        obj, detail = convert_with_error_detail({'x': 'bad', 'y': 2}, Point)

        assert detail.loc == ('x',)
        assert detail.error_value == 'bad'
        assert resolve_calls.calls == 1
