"""
Benchmark for the argument resolution of the public API families.

Every family accepts either a typehint or a `msgspec` decoder, which are
resolved into a target type and a `convert` options dict. That dict is only
read by a repair, so the load families pick the decoder before the decode and
resolve the model and the options only after it failed. The rows below keep the
cost of each step visible:

- the baselines: a prebuilt `msgspec.json.Decoder` and the one-shot
  `msgspec.json.decode()`
- the isolated steps: `.type`, `.strict`, `.dec_hook`, the exact class check,
  and the options dict `get_decoder_model()` builds
- the public functions, on the success path and on the failure path

The target is a single-level struct, the smallest useful decode of the library,
so the fixed cost of the wrapper is a large share of the total.

Run with `python -m tests.benchmark.bench_decoder_model`.
"""
import timeit

import msgspec
from msgspec.json import Decoder as JsonDecoder, decode as decode_json
from msgspec.msgpack import Decoder as MsgpackDecoder

from msgspecerror import (
    load_json_with_default, load_json_with_error, load_json_with_error_detail, load_msgpack_with_default,
    load_msgpack_with_error, load_msgpack_with_error_detail
)
from msgspecerror.parse_decoder import get_decoder_model


class User(msgspec.Struct):
    """Single-level struct, the benchmark target."""
    id: int
    name: str
    age: int


JSON_OK = b'{"id": 1, "name": "alice", "age": 30}'
JSON_BAD = b'{"id": "bad", "name": "alice", "age": 30}'
MSGPACK_OK = msgspec.msgpack.encode({'id': 1, 'name': 'alice', 'age': 30})
MSGPACK_BAD = msgspec.msgpack.encode({'id': 'bad', 'name': 'alice', 'age': 30})

DECODER = JsonDecoder(User)
MSGPACK_DECODER = MsgpackDecoder(User)

# Wall clock budget of a single timing run, the iteration count of each row is
# derived from its own cost so a nanosecond row and a repair row are measured
# in the same amount of time
TARGET_SECONDS = 0.05
MAX_NUMBER = 200_000
REPEAT = 7


def noop(*args):
    """Python call floor for the per-iteration rows."""
    return None


STEP_CASES = [
    ('noop() call floor', 'noop(JSON_OK)'),
    ('Decoder.decode', 'DECODER.decode(JSON_OK)'),
    ('json.decode(type=User)', 'decode_json(JSON_OK, type=User)'),
    ('msgpack Decoder.decode', 'MSGPACK_DECODER.decode(MSGPACK_OK)'),
    ('Decoder.type read', 'DECODER.type'),
    ('Decoder.strict read', 'DECODER.strict'),
    ('Decoder.dec_hook read', 'DECODER.dec_hook'),
    ('class check only', 'type(DECODER) is JsonDecoder'),
    ('options dict, no attrs', "{'strict': True, 'dec_hook': None, 'str_keys': True}"),
    ('options dict, attrs read', "{'strict': DECODER.strict, 'dec_hook': DECODER.dec_hook, 'str_keys': True}"),
    ('get_decoder_model(User)', 'get_decoder_model(User)'),
    ('get_decoder_model(Decoder)', 'get_decoder_model(DECODER)'),
    ('get_decoder_model(Decoder, default)', "get_decoder_model(DECODER, JsonDecoder, {'str_keys': True}, 'x')"),
]

SUCCESS_CASES = [
    ('load_json_with_error(User)', 'load_json_with_error(JSON_OK, User)'),
    ('load_json_with_error(Decoder)', 'load_json_with_error(JSON_OK, DECODER)'),
    ('load_msgpack_with_error(User)', 'load_msgpack_with_error(MSGPACK_OK, User)'),
    ('load_msgpack_with_error(Decoder)', 'load_msgpack_with_error(MSGPACK_OK, MSGPACK_DECODER)'),
    ('load_json_with_error_detail(User)', 'load_json_with_error_detail(JSON_OK, User)'),
    ('load_json_with_error_detail(Decoder)', 'load_json_with_error_detail(JSON_OK, DECODER)'),
    ('load_msgpack_with_error_detail(User)', 'load_msgpack_with_error_detail(MSGPACK_OK, User)'),
    ('load_msgpack_with_error_detail(Decoder)', 'load_msgpack_with_error_detail(MSGPACK_OK, MSGPACK_DECODER)'),
    ('load_json_with_default(User)', 'load_json_with_default(JSON_OK, User)'),
    ('load_json_with_default(Decoder)', 'load_json_with_default(JSON_OK, DECODER)'),
    ('load_msgpack_with_default(User)', 'load_msgpack_with_default(MSGPACK_OK, User)'),
    ('load_msgpack_with_default(Decoder)', 'load_msgpack_with_default(MSGPACK_OK, MSGPACK_DECODER)'),
]

FAILURE_CASES = [
    ('load_json_with_error(User)', 'load_json_with_error(JSON_BAD, User)'),
    ('load_json_with_error(Decoder)', 'load_json_with_error(JSON_BAD, DECODER)'),
    ('load_json_with_error_detail(User)', 'load_json_with_error_detail(JSON_BAD, User)'),
    ('load_json_with_error_detail(Decoder)', 'load_json_with_error_detail(JSON_BAD, DECODER)'),
    ('load_json_with_default(User)', 'load_json_with_default(JSON_BAD, User)'),
    ('load_json_with_default(Decoder)', 'load_json_with_default(JSON_BAD, DECODER)'),
    ('load_msgpack_with_error_detail(User)', 'load_msgpack_with_error_detail(MSGPACK_BAD, User)'),
    ('load_msgpack_with_error_detail(Decoder)', 'load_msgpack_with_error_detail(MSGPACK_BAD, MSGPACK_DECODER)'),
    ('load_msgpack_with_default(User)', 'load_msgpack_with_default(MSGPACK_BAD, User)'),
    ('load_msgpack_with_default(Decoder)', 'load_msgpack_with_default(MSGPACK_BAD, MSGPACK_DECODER)'),
]


def measure(stmt, target_seconds=TARGET_SECONDS, repeat=REPEAT, max_number=MAX_NUMBER):
    """
    Time a statement with an iteration count derived from its own cost.

    A cheap row runs many times, an expensive one runs few, so every row costs
    about `target_seconds` per timing run and the table stays quick.

    Args:
        stmt (str): Statement to execute
        target_seconds (float): Wall clock budget of one timing run
        repeat (int): Timing runs, the best one is kept
        max_number (int): Upper limit of executions per timing run

    Returns:
        float: Nanoseconds per execution
    """
    timer = timeit.Timer(stmt, globals=globals())
    probe = min(timer.repeat(repeat=3, number=1))
    number = max(1, min(max_number, int(target_seconds / max(probe, 1e-9))))
    return min(timer.repeat(repeat=repeat, number=number)) / number * 1e9


def verify_outputs():
    """Every row has to produce the same object the raw decode produces."""
    expected = User(id=1, name='alice', age=30)

    for label, stmt in SUCCESS_CASES:
        result = eval(stmt, globals())
        obj = result[0]
        if obj != expected:
            raise AssertionError(f'{label} returned {obj!r}')

    for label, stmt in FAILURE_CASES:
        obj, reported = eval(stmt, globals())
        if obj is not msgspec.NODEFAULT:
            raise AssertionError(f'{label} returned an object for a failing payload')
        if not reported:
            raise AssertionError(f'{label} reported no error')


def print_rows(title, cases, baseline):
    """
    Print one table of measured rows.

    Args:
        title (str): Table title
        cases (list): (label, statement) pairs
        baseline (float): Nanoseconds of the reference row, for the ratio column
    """
    print()
    print(title)
    print('-' * 68)
    for label, stmt in cases:
        nanos = measure(stmt)
        print(f'{label:<38} {nanos:>10.1f} {nanos / baseline:>17.2f}x')


def main():
    """Measure the isolated steps, the success path and the failure path."""
    verify_outputs()

    print(f'Single-level struct, {len(JSON_OK)} byte json payload, '
          f'per-row iteration count, {REPEAT} runs, best kept')
    print(f"{'case':<38} {'ns/call':>10} {'x Decoder.decode':>18}")
    print('-' * 68)

    steps = [(label, measure(stmt)) for label, stmt in STEP_CASES]
    baseline = dict(steps)['Decoder.decode']
    for label, nanos in steps:
        print(f'{label:<38} {nanos:>10.1f} {nanos / baseline:>17.2f}x')

    print_rows('Success path: what a normal decode costs', SUCCESS_CASES, baseline)
    print_rows('Failure path: what a reported error costs', FAILURE_CASES, baseline)

    by_label = dict(steps)
    print()
    print('Off the success path, still paid by a failure:')
    for label in ('get_decoder_model(User)', 'get_decoder_model(Decoder)', 'get_decoder_model(Decoder, default)'):
        print(f'  {label}: {by_label[label]:.1f}ns')


if __name__ == '__main__':
    main()
