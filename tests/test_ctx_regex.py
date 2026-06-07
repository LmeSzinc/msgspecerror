"""
Comprehensive tests for revert_regex() via real msgspec decode.

Every test triggers an actual msgspec ``ValidationError`` by decoding
data that violates a pattern constraint, then verifies the extracted
``err.ctx.pattern`` matches the original.
"""
import re

import msgspec
import pytest
from typing_extensions import Annotated

from msgspecerror import parse_msgspec_error
from msgspecerror.parse_ctx import revert_regex


def _violate(pattern, bad=b'"__VIOLATE__"'):
    """Build an Annotated type + decode bad data, return parsed error."""
    T = Annotated[str, msgspec.Meta(pattern=pattern)]
    try:
        msgspec.json.decode(bad, type=T)
    except msgspec.ValidationError as e:
        return parse_msgspec_error(e)
    raise AssertionError(f"pattern={pattern!r} did not reject {bad!r}")


class TestPlainPatterns:
    """Patterns without backslashes — no repr escaping needed."""

    @pytest.mark.parametrize("pattern, bad", [
        (r"hello", b'"__VIOLATE__"'),
        (r"abc|def", b'"xyz"'),
        (r"[0-9]+", b'"abc"'),
        (r"[a-zA-Z_][a-zA-Z0-9_]*", b'"123"'),
        (r"^hello$", b'"__VIOLATE__"'),
    ])
    def test_plain(self, pattern, bad):
        err = _violate(pattern, bad)
        assert err.ctx.pattern == pattern, err.ctx.pattern


class TestBackslashPatterns:
    """Patterns with backslash escapes — repr doubles them.

    Note: standalone anchors (``\\b``, ``\\B``, ``\\A``, ``\\Z``) and
    ``\\s*`` match too broadly to be violated by any input in msgspec's
    ``re.search`` mode, so they are excluded.
    """

    @pytest.mark.parametrize("pattern, bad", [
        (r"\d+", b'"abc"'),
        (r"\w+", b'"!!!"'),
        (r"\d{4}-\d{2}-\d{2}", b'"abc"'),
        (r"^\d+$", b'"abc"'),
        (r"\d+\.\d+", b'"abc"'),
        (r"\w+@\w+\.\w+", b'"!!!"'),
        (r"https?://\w+\.\w+", b'"!!!"'),
    ])
    def test_backslash(self, pattern, bad):
        err = _violate(pattern, bad)
        assert err.ctx.pattern == pattern, f"got {err.ctx.pattern!r}"


class TestLiteralBackslashPatterns:
    """Patterns with literal backslash (\\\\ in raw = one \\ in regex)."""

    def test_path_to_file(self):
        """Pattern: ``path\\to\\file`` matches ``path\\to\\file`` literally."""
        err = _violate(r"path\\to\\file")
        assert err.ctx.pattern == r"path\\to\\file"

    def test_server_share(self):
        """Pattern: ``\\\\server\\share`` (leading double backslash)."""
        err = _violate(r"\\\\server\\share")
        assert err.ctx.pattern == r"\\\\server\\share"

    def test_mixed_backslash(self):
        """Pattern with both backslash-escapes and literal backslashes."""
        err = _violate(r"hello\\sworld")
        assert err.ctx.pattern == r"hello\\sworld"

    def test_leading_literal_backslash(self):
        err = _violate(r"\\start")
        assert err.ctx.pattern == r"\\start"

    def test_trailing_literal_backslash(self):
        err = _violate(r"end\\")
        assert err.ctx.pattern == r"end\\"


class TestMixedPatterns:
    """Combinations of regex escapes, groups, and quantifiers."""

    @pytest.mark.parametrize("pattern", [
        r"(foo|bar)+",
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        r"^^\d+$$",
    ])
    def test_mixed(self, pattern):
        err = _violate(pattern)
        assert err.ctx.pattern == pattern, f"got {err.ctx.pattern!r}"


class TestQuotePatterns:
    """Patterns containing quotes — repr wrapping depends on content."""

    def test_single_quote(self):
        """Pattern ``it's`` — repr uses double-quote wrapping."""
        err = _violate(r"it's")
        assert err.ctx.pattern == r"it's"

    def test_double_quote(self):
        """Pattern ``he"llo`` — repr uses single-quote wrapping."""
        err = _violate(r'he"llo')
        assert err.ctx.pattern == r'he"llo'

    def test_multiple_single_quotes(self):
        """Pattern ``a'b'c`` — repr uses double-quote wrapping."""
        err = _violate(r"a'b'c")
        assert err.ctx.pattern == r"a'b'c"

    def test_quoted_word(self):
        """Pattern ``"quoted"`` — repr uses single-quote wrapping."""
        err = _violate(r'"quoted"')
        assert err.ctx.pattern == r'"quoted"'


class TestEscapedQuotePatterns:
    """Patterns with escaped quotes (\\') via single-quote repr wrapping."""

    def test_escaped_single_quote(self):
        """Pattern ``'suffix`` — has both ' and \\ ; repr uses '...' and escapes '."""
        err = _violate(r"\d+'suffix")
        assert err.ctx.pattern == r"\d+'suffix"


class TestRevertRegression:
    """Direct revert_regex tests — verifies the order-of-operations fix."""

    def test_no_escaping_unchanged(self):
        """A plain pattern round-trips through revert_regex itself."""
        assert revert_regex(r"^hello$") == r"^hello$"
        assert revert_regex(r"[0-9]+")  == r"[0-9]+"
        assert revert_regex(r"abc|def") == r"abc|def"

    def test_backslash_quote_preserved(self):
        """Pattern ``\\'`` (backslash + quote) must not lose the backslash.

        ``repr("\\'")`` = ``"\\\\'"``  (double-quoted, backslash escaped).
        revert_regex must produce the original ``\\'``.
        Before the order-of-operations fix this collapsed to just ``'``.
        """
        fragment = repr(r"\'")
        result = revert_regex(fragment)
        assert result == r"\'", f"expected \\' got {result!r}"

    def test_backslash_only_preserved(self):
        """Pattern ``\\`` (single backslash) must survive the round-trip."""
        fragment = repr(re.compile(r"\\").pattern)
        result = revert_regex(fragment)
        assert result == r"\\", f"expected \\\\ got {result!r}"

    def test_repr_roundtrip_identity(self):
        """For several patterns, repr(revert_regex(repr(pattern))) == repr(pattern)."""
        for pattern in [r"hello", r"\d+", r"it's", r'he"llo', r"\'", r"\bdigit\b"]:
            compiled = re.compile(pattern).pattern
            quoted = repr(compiled)
            reverted = revert_regex(quoted)
            assert reverted == pattern, f"pattern={pattern!r} got={reverted!r}"
