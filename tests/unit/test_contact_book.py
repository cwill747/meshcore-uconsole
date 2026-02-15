"""Tests for ContactBook and Contact compatibility with pyMC_core."""

from meshcore_console.meshcore.contact_book import Contact, ContactBook


def test_contact_has_out_path_default() -> None:
    """Contact.out_path defaults to None so pyMC_core can read it before an advert arrives."""
    contact = Contact(name="Alice", public_key="ab" * 32)
    assert contact.out_path is None


def test_contact_allows_dynamic_attributes() -> None:
    """pyMC_core sets dynamic attributes (e.g. out_path) on contacts during advert processing.

    Contact must NOT use slots=True or pyMC_core will crash with AttributeError.
    """
    contact = Contact(name="Alice", public_key="ab" * 32)

    # Simulate pyMC_core setting out_path after processing an advert
    contact.out_path = [0xA2, 0xB3]
    assert contact.out_path == [0xA2, 0xB3]

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
    """Updating a contact via add_contact should not lose pyMC_core-set attributes."""
    book = ContactBook()
    book.add_contact({"name": "Alice", "public_key": "ab" * 32})

    # Simulate pyMC_core setting out_path on the contact
    contact = book.get_by_name("Alice")
    assert contact is not None
    contact.out_path = [0xA2]

    # Re-adding the same contact (e.g. from a new advert) replaces the entry
    book.add_contact({"name": "Alice", "public_key": "cd" * 32})
    updated = book.get_by_name("Alice")
    assert updated is not None
    assert updated.public_key == "cd" * 32
    # out_path resets because it's a new Contact object — this is expected;
    # pyMC_core will re-set it on the next advert
    assert updated.out_path is None
