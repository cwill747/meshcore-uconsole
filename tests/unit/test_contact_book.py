"""Tests for ContactBook and Contact compatibility with pyMC_core."""

from meshcore_console.meshcore.contact_book import Contact, ContactBook


def test_contact_has_out_path_default() -> None:
    """Contact.out_path defaults to None so pyMC_core can read it before an advert arrives."""
    contact = Contact(name="Alice", public_key="ab" * 32)
    assert contact.out_path is None
    assert contact.out_path_len == -1


def test_contact_allows_dynamic_attributes() -> None:
    """pyMC_core sets dynamic attributes on contacts during advert processing.

    Contact must NOT use slots=True or pyMC_core will crash with AttributeError.
    """
    contact = Contact(name="Alice", public_key="ab" * 32)

    contact.out_path = b"\xa2\xb3"
    contact.out_path_len = 2
    assert contact.out_path == b"\xa2\xb3"
    assert contact.out_path_len == 2

    # pyMC_core may also set other dynamic attributes we don't declare
    contact.last_rssi = -72  # type: ignore[attr-defined]
    assert contact.last_rssi == -72  # type: ignore[attr-defined]


def test_contact_book_add_and_lookup() -> None:
    book = ContactBook()
    book.add_contact({"name": "Alice", "public_key": "ab" * 32})

    contact = book.get_by_name("Alice")
    assert contact is not None
    assert contact.name == "Alice"
    assert contact.out_path is None


def test_contact_book_update_preserves_out_path() -> None:
    """Updating a contact via add_contact preserves existing path data."""
    book = ContactBook()
    book.add_contact({"name": "Alice", "public_key": "ab" * 32})

    contact = book.get_by_name("Alice")
    assert contact is not None
    contact.out_path = b"\xa2"
    contact.out_path_len = 1

    # Re-adding with a dict (no path info) preserves existing path
    book.add_contact({"name": "Alice", "public_key": "cd" * 32})
    updated = book.get_by_name("Alice")
    assert updated is not None
    assert updated.public_key == "cd" * 32
    assert updated.out_path == b"\xa2"
    assert updated.out_path_len == 1

    # Re-adding with a Contact that has explicit path overwrites
    new_contact = Contact(name="Alice", public_key="ef" * 32, out_path=b"", out_path_len=0)
    book.add_contact(new_contact)
    updated2 = book.get_by_name("Alice")
    assert updated2 is not None
    assert updated2.out_path == b""
    assert updated2.out_path_len == 0
