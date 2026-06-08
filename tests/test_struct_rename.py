from msgspec import Struct

from msgspecerror.parse_struct import get_field_name


# Structs for testing rename strategies.
class RenameLower(Struct, rename="lower"):
    user_id: int
    apiKey: str
    XMLParser: int
    simple: str
    a: int


class RenameUpper(Struct, rename="upper"):
    some_field: int
    another_field: str
    user_id: int


class RenameCamel(Struct, rename="camel"):
    some_field: int
    another_field: str
    user_id: int
    api_key: str
    XMLParser: int


class RenamePascal(Struct, rename="pascal"):
    some_field: int
    another_field: str
    user_id: int
    api_key: str


class RenameKebab(Struct, rename="kebab"):
    some_field: int
    another_field: str
    user_id: int


class RenameCallable(Struct, rename=lambda x: x.upper()):
    some_field: int
    anotherField: str


class RenameCallableNone(Struct, rename=lambda x: None):
    some_field: int
    anotherField: str


class RenameMapping(Struct, rename={"some_field": "X", "anotherField": "Y"}):
    some_field: int
    anotherField: str


class RenameNone(Struct, rename=None):
    some_field: int
    anotherField: str


class ParentRenameLower(Struct, rename="lower"):
    parent_field: int
    ParentFieldTwo: str


class ChildOfRenameLower(ParentRenameLower):
    child_field: int


class TestGetFieldNameWithRename:
    """Tests for `get_field_name` with various rename strategies."""

    # ------------------------------------------------------------------
    # rename strategy: lower
    # ------------------------------------------------------------------
    def test_rename_lower_encode_to_field(self):
        """rename='lower': encode name resolves to field name."""
        assert get_field_name(RenameLower, "apikey") == "apiKey"
        assert get_field_name(RenameLower, "xmlparser") == "XMLParser"

    def test_rename_lower_field_to_field(self):
        """rename='lower': field name returns itself."""
        assert get_field_name(RenameLower, "user_id") == "user_id"
        assert get_field_name(RenameLower, "apiKey") == "apiKey"
        assert get_field_name(RenameLower, "XMLParser") == "XMLParser"

    def test_rename_lower_already_lowercase(self):
        """rename='lower': field already lowercase, encode name unchanged."""
        assert get_field_name(RenameLower, "user_id") == "user_id"
        assert get_field_name(RenameLower, "simple") == "simple"
        assert get_field_name(RenameLower, "a") == "a"

    # ------------------------------------------------------------------
    # rename strategy: upper
    # ------------------------------------------------------------------
    def test_rename_upper_encode_to_field(self):
        """rename='upper': encode name resolves to field name."""
        assert get_field_name(RenameUpper, "SOME_FIELD") == "some_field"
        assert get_field_name(RenameUpper, "ANOTHER_FIELD") == "another_field"
        assert get_field_name(RenameUpper, "USER_ID") == "user_id"

    def test_rename_upper_field_to_field(self):
        """rename='upper': field name returns itself."""
        assert get_field_name(RenameUpper, "some_field") == "some_field"
        assert get_field_name(RenameUpper, "another_field") == "another_field"

    # ------------------------------------------------------------------
    # rename strategy: camel
    # ------------------------------------------------------------------
    def test_rename_camel_encode_to_field(self):
        """rename='camel': encode name resolves to field name."""
        assert get_field_name(RenameCamel, "someField") == "some_field"
        assert get_field_name(RenameCamel, "anotherField") == "another_field"
        assert get_field_name(RenameCamel, "userId") == "user_id"
        assert get_field_name(RenameCamel, "apiKey") == "api_key"

    def test_rename_camel_field_to_field(self):
        """rename='camel': field name returns itself."""
        assert get_field_name(RenameCamel, "some_field") == "some_field"
        assert get_field_name(RenameCamel, "another_field") == "another_field"

    def test_rename_camel_already_camel(self):
        """rename='camel': field already camelCase, encode name unchanged."""
        assert get_field_name(RenameCamel, "XMLParser") == "XMLParser"

    # ------------------------------------------------------------------
    # rename strategy: pascal
    # ------------------------------------------------------------------
    def test_rename_pascal_encode_to_field(self):
        """rename='pascal': encode name resolves to field name."""
        assert get_field_name(RenamePascal, "SomeField") == "some_field"
        assert get_field_name(RenamePascal, "AnotherField") == "another_field"
        assert get_field_name(RenamePascal, "UserId") == "user_id"
        assert get_field_name(RenamePascal, "ApiKey") == "api_key"

    def test_rename_pascal_field_to_field(self):
        """rename='pascal': field name returns itself."""
        assert get_field_name(RenamePascal, "some_field") == "some_field"
        assert get_field_name(RenamePascal, "api_key") == "api_key"

    # ------------------------------------------------------------------
    # rename strategy: kebab
    # ------------------------------------------------------------------
    def test_rename_kebab_encode_to_field(self):
        """rename='kebab': encode name resolves to field name."""
        assert get_field_name(RenameKebab, "some-field") == "some_field"
        assert get_field_name(RenameKebab, "another-field") == "another_field"
        assert get_field_name(RenameKebab, "user-id") == "user_id"

    def test_rename_kebab_field_to_field(self):
        """rename='kebab': field name returns itself."""
        assert get_field_name(RenameKebab, "some_field") == "some_field"
        assert get_field_name(RenameKebab, "another_field") == "another_field"

    # ------------------------------------------------------------------
    # rename strategy: callable
    # ------------------------------------------------------------------
    def test_rename_callable_encode_to_field(self):
        """rename=callable: encode name resolves to field name."""
        assert get_field_name(RenameCallable, "SOME_FIELD") == "some_field"
        assert get_field_name(RenameCallable, "ANOTHERFIELD") == "anotherField"

    def test_rename_callable_field_to_field(self):
        """rename=callable: field name returns itself."""
        assert get_field_name(RenameCallable, "some_field") == "some_field"
        assert get_field_name(RenameCallable, "anotherField") == "anotherField"

    # ------------------------------------------------------------------
    # rename strategy: callable returning None
    # ------------------------------------------------------------------
    def test_rename_callable_none_encode_to_field(self):
        """rename=callable returning None: encode name == field name."""
        assert get_field_name(RenameCallableNone, "some_field") == "some_field"
        assert get_field_name(RenameCallableNone, "anotherField") == "anotherField"

    # ------------------------------------------------------------------
    # rename strategy: mapping (dict)
    # ------------------------------------------------------------------
    def test_rename_mapping_encode_to_field(self):
        """rename=mapping: mapped key resolves to field name."""
        assert get_field_name(RenameMapping, "X") == "some_field"
        assert get_field_name(RenameMapping, "Y") == "anotherField"

    def test_rename_mapping_field_to_field(self):
        """rename=mapping: field name returns itself."""
        assert get_field_name(RenameMapping, "some_field") == "some_field"
        assert get_field_name(RenameMapping, "anotherField") == "anotherField"

    # ------------------------------------------------------------------
    # rename strategy: None
    # ------------------------------------------------------------------
    def test_rename_none_encode_to_field(self):
        """rename=None: encode name == field name."""
        assert get_field_name(RenameNone, "some_field") == "some_field"
        assert get_field_name(RenameNone, "anotherField") == "anotherField"

    # ------------------------------------------------------------------
    # rename with inheritance
    # ------------------------------------------------------------------
    def test_rename_inherited_encode_to_field(self):
        """rename on parent struct: child inherits rename, encode->field."""
        assert get_field_name(ChildOfRenameLower, "parent_field") == "parent_field"
        assert get_field_name(ChildOfRenameLower, "parentfieldtwo") == "ParentFieldTwo"

    def test_rename_inherited_field_to_field(self):
        """rename on parent struct: child field name returns itself."""
        assert get_field_name(ChildOfRenameLower, "parent_field") == "parent_field"
        assert get_field_name(ChildOfRenameLower, "ParentFieldTwo") == "ParentFieldTwo"

    def test_rename_inherited_child_field(self):
        """rename on parent struct: child's own field follows parent rename."""
        assert get_field_name(ChildOfRenameLower, "child_field") == "child_field"
