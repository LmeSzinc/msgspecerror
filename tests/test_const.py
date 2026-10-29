"""
Tests for the version helpers and constants in const.py.
"""
import msgspec
import pytest

from msgspecerror.const import MSGSPEC_0_19_PLUS, MSGSPEC_0_22_PLUS, MSGSPEC_MAJOR_VERSION, _parse_msgspec_version


class TestParseMsgspecVersion:
    """_parse_msgspec_version() should keep only the leading numeric components."""

    @pytest.mark.parametrize("version, expected", [
        # plain release versions
        ('0.18.6', (0, 18, 6)),
        ('0.19.0', (0, 19, 0)),
        ('0.22.0', (0, 22, 0)),
        ('1.2.3', (1, 2, 3)),
        # multi-digit components
        ('10.20.30', (10, 20, 30)),
        ('0.100.2000', (0, 100, 2000)),
        # leading zeros are dropped by int()
        ('00.019.002', (0, 19, 2)),
        # a `dev` suffix is cut from the component it is attached to
        ('0.19dev1', (0, 19)),
        ('0.19.0.dev27', (0, 19, 0)),
        ('1.2.3dev4', (1, 2, 3)),
        ('0.21.2.dev27+gebdc7337a', (0, 21, 2)),
        ('0.21.2.dev27+gebdc7337a.d20260606', (0, 21, 2)),
        # the local version segment after the first `+` is ignored, even when
        # it looks like numbered components
        ('0.19.0+local', (0, 19, 0)),
        ('0.19.0+1.2.3', (0, 19, 0)),
        ('0.19.0+abc+def', (0, 19, 0)),
        # at most three components are compared
        ('1.2.3.4', (1, 2, 3)),
        ('1.2.3.999.888', (1, 2, 3)),
        # a non-numeric component stops the parse, the numeric prefix is kept
        ('0.18.0rc1', (0, 18)),
        ('0.19.0rc1', (0, 19)),
        ('0.19.rc1', (0, 19)),
        ('1.2.beta.4', (1, 2)),
        # a chunk starting with `dev` has no digits in front of it
        ('dev1.2.3', ()),
        ('0.dev1.2', (0,)),
        # empty or non-numeric version strings yield an empty tuple
        ('', ()),
        ('.', ()),
        ('0..19', (0,)),
        ('v0.19.0', ()),
        ('release', ()),
        # a leading separator leaves nothing to parse
        ('+0.19.0', ()),
        ('.19.0', ()),
        # a non-decimal digit stops the parse: '²'.isdigit() is True but
        # int('²') raises ValueError
        ('1.².3', (1,)),
        ('0.19.²', (0, 19)),
        ('²', ()),
        # short version strings are not padded with zeros
        ('0', (0,)),
        ('0.19', (0, 19)),
        ('1.2', (1, 2)),
    ])
    def test_parse(self, version, expected):
        result = _parse_msgspec_version(version)
        assert result == expected
        assert isinstance(result, tuple)
        assert all(isinstance(part, int) for part in result)


class TestVersionComparison:
    """The parsed tuple should compare against (0, 19) without zero padding."""

    @pytest.mark.parametrize("version, expected", [
        # releases before 0.19
        ('0.18.6', False),
        ('0.18.99', False),
        ('0.18.0rc1', False),
        ('0.18.0.dev1', False),
        ('0', False),
        ('', False),
        # 0.19 and later
        ('0.19', True),
        ('0.19.0', True),
        ('0.19.1', True),
        ('0.20.0', True),
        ('1.0.0', True),
        ('0.19.0+local', True),
        ('0.21.2.dev27+gebdc7337a.d20260606', True),
        # a dev build of 0.19 already carries the 0.19 features
        ('0.19.0.dev1', True),
        # a pre-release of 0.19.0 parses to (0, 19), the pre-release marker is
        # dropped by the numeric prefix, so it counts as 0.19 and later
        ('0.19.0rc1', True),
    ])
    def test_against_0_19(self, version, expected):
        assert (_parse_msgspec_version(version) >= (0, 19)) is expected


class TestMsgSpecMajorVersion:
    """MSGSPEC_MAJOR_VERSION is the parsed version every flag compares against."""

    def test_is_the_parsed_installed_version(self):
        assert MSGSPEC_MAJOR_VERSION == _parse_msgspec_version(msgspec.__version__)

    def test_is_a_tuple_of_ints(self):
        assert isinstance(MSGSPEC_MAJOR_VERSION, tuple)
        assert all(isinstance(part, int) for part in MSGSPEC_MAJOR_VERSION)

    def test_is_parsed_once(self):
        """The flags read the constant, they do not re-parse the version."""
        assert MSGSPEC_0_19_PLUS == (MSGSPEC_MAJOR_VERSION >= (0, 19))
        assert MSGSPEC_0_22_PLUS == (MSGSPEC_MAJOR_VERSION >= (0, 22))


class TestMsgSpec019Plus:
    """MSGSPEC_0_19_PLUS should follow the installed msgspec version."""

    def test_is_bool(self):
        assert isinstance(MSGSPEC_0_19_PLUS, bool)

    def test_matches_the_installed_version(self):
        assert MSGSPEC_0_19_PLUS == (MSGSPEC_MAJOR_VERSION >= (0, 19))


class TestMsgSpec022Plus:
    """MSGSPEC_0_22_PLUS should follow the installed msgspec version."""

    def test_is_bool(self):
        assert isinstance(MSGSPEC_0_22_PLUS, bool)

    def test_matches_the_installed_version(self):
        assert MSGSPEC_0_22_PLUS == (MSGSPEC_MAJOR_VERSION >= (0, 22))

    @pytest.mark.parametrize('version, expected', [
        # the release that names a `Raw` target `raw`
        ('0.22.0', True),
        ('0.22.1', True),
        ('0.23.0', True),
        ('1.0.0', True),
        ('0.22.0+local', True),
        # before it a `Raw` target shares the typecode of `Any`
        ('0.21.2.dev27+gebdc7337a', False),
        ('0.21.1', False),
        ('0.21.0', False),
        ('0.19.0', False),
        ('0.18.6', False),
        ('0.22', True),
        ('0.21', False),
    ])
    def test_against_0_22(self, version, expected):
        assert (_parse_msgspec_version(version) >= (0, 22)) is expected
