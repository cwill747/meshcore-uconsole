"""Contact book adapter for pyMC_core.

pyMC_core handlers (TextMessageHandler, LoginResponseHandler, etc.) expect
a contact book object with:
  - .contacts  — iterable of objects with .public_key (hex str) and .name
  - .get_by_name(name) — return a contact or None
  - .add_contact(data) — store a new contact
  - .list_contacts() — return all contacts (used by ProtocolResponseHandler)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Contact:
    """Single contact entry compatible with pyMC_core handler expectations.

    Note: slots=False is intentional — pyMC_core sets dynamic attributes
    (e.g. out_path) on contacts when processing adverts.
    """

    name: str
    public_key: str  # 64-char hex string
    out_path: bytes | None = None
    out_path_len: int = -1  # -1 = unknown → flood; 0 = direct; >0 = encoded hop count


class ContactBook:
    """In-memory contact book that satisfies the pyMC_core contacts interface."""

    def __init__(self) -> None:
        self.contacts: list[Contact] = []

    def list_contacts(self) -> list[Contact]:
        return self.contacts

    def get_by_name(self, name: str) -> Contact | None:
        for contact in self.contacts:
            if contact.name == name:
                return contact
        return None

    def get_by_key(self, public_key: bytes | str) -> Contact | None:
        if isinstance(public_key, bytes):
            public_key = public_key.hex()
        for contact in self.contacts:
            if contact.public_key == public_key:
                return contact
        return None

    def update(self, contact: Contact) -> None:
        for i, existing in enumerate(self.contacts):
            if existing.public_key == contact.public_key:
                self.contacts[i] = contact
                return

    def add_contact(self, data: dict[str, str] | Contact) -> None:
        if isinstance(data, Contact):
            entry = data
        else:
            entry = Contact(name=data.get("name", ""), public_key=data.get("public_key", ""))
        if not entry.name or not entry.public_key:
            return
        for i, existing in enumerate(self.contacts):
            if existing.name == entry.name:
                existing.public_key = entry.public_key
                if entry.out_path is not None:
                    existing.out_path = entry.out_path
                    existing.out_path_len = entry.out_path_len
                return
        self.contacts.append(entry)
