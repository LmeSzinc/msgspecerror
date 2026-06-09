"""
Benchmark for load_msgpack_with_default across common repair scenarios.

Scenarios (same structure as JSON benchmarks, but msgpack-encoded):
1. No errors (happy path)
2. Error at struct field  — one field has wrong type, repaired to default
3. Error at dict value    — dict value fails validation, repaired via ``...`` path
4. Error at list item     — list item fails validation, popped from result
5. Unicode decode error   — invalid UTF-8 in a msgpack string payload
"""
from typing import Dict, List

import msgspec

from msgspecerror import load_msgpack_with_default
from tests.benchmark.perf import PerformanceTest


# ------------------------------------------------------------------
# Model classes — named so the Parameters column shows what scenario
# is being repaired (module-level to avoid long <locals> paths).
# ------------------------------------------------------------------


class HappyPathModel(msgspec.Struct):
    """Valid data, no repair needed."""
    name: str
    count: int
    enabled: bool


class StructFieldModel(msgspec.Struct):
    """One field has a wrong type; repair sets it to the field default."""
    name: str = "default"
    count: int = 0
    enabled: bool = False


class DictValueModel(msgspec.Struct):
    """A dict value fails validation; repair locates the failing key via ``...``."""
    mapping: Dict[str, int] = {}


class ListItemModel(msgspec.Struct):
    """A list item fails validation; repair pops the offending index."""
    values: List[int]


# ------------------------------------------------------------------
# Scenario helpers — each returns (data, model)
# ------------------------------------------------------------------

def scenario_happy_path():
    """Valid msgpack -> no errors, happy path."""
    data = msgspec.msgpack.encode({"name": "test", "count": 42, "enabled": True})
    return data, HappyPathModel


def scenario_struct_field():
    """One struct field has wrong type -> repaired to field default."""
    data = msgspec.msgpack.encode({"name": "test", "count": "bad", "enabled": True})
    return data, StructFieldModel


def scenario_dict_value():
    """Dict value fails type check -> repaired with ``...`` path."""
    data = msgspec.msgpack.encode({"mapping": {"a": 1, "b": "bad", "c": 3}})
    return data, DictValueModel


def scenario_list_item():
    """List item fails type check -> item popped from list."""
    data = msgspec.msgpack.encode({"values": [1, "bad", 3]})
    return data, ListItemModel


def scenario_unicode_error():
    """Msgpack string with invalid UTF-8 -> repaired with utf8_error='ignore'."""
    data = (
        b'\x83'  # fixmap 3
        b'\xa4name'  # str(4) "name"
        b'\xa5test\xff'  # str(5) "test\xff"  <- invalid UTF-8
        b'\xa5count'  # str(5) "count"
        b'\x2a'  # positive fixint 42
        b'\xa7enabled'  # str(7) "enabled"
        b'\xc3'  # true
    )
    return data, HappyPathModel


def scenario_unicode_replace():
    """Msgpack string with invalid UTF-8 -> repaired with utf8_error='replace'."""
    data = (
        b'\x83'
        b'\xa4name'
        b'\xa5test\xff'
        b'\xa5count'
        b'\x2a'
        b'\xa7enabled'
        b'\xc3'
    )
    return data, HappyPathModel


# ------------------------------------------------------------------
# Register all scenarios
# ------------------------------------------------------------------

def register_scenarios(pref):
    """Register all msgpack repair scenarios."""
    data, model = scenario_happy_path()
    pref.register(load_msgpack_with_default, data, model)

    data, model = scenario_struct_field()
    pref.register(load_msgpack_with_default, data, model)

    data, model = scenario_dict_value()
    pref.register(load_msgpack_with_default, data, model)

    data, model = scenario_list_item()
    pref.register(load_msgpack_with_default, data, model)

    # unicode_error uses utf8_error='ignore' so verification output doesn't
    # contain U+FFFD (which crashes print() in a GBK terminal)
    data, model = scenario_unicode_error()
    pref.register(load_msgpack_with_default, data, model, utf8_error='ignore')

    data, model = scenario_unicode_replace()
    pref.register(load_msgpack_with_default, data, model, utf8_error='replace')


if __name__ == "__main__":
    with PerformanceTest() as pref:
        register_scenarios(pref)
