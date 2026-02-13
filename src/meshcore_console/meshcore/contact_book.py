"""Contact book adapter for pyMC_core.

pyMC_core handlers (TextMessageHandler, LoginResponseHandler, etc.) expect
a contact book object with:
  - .contacts  — iterable of objects with .public_key (hex str) and .name
  - .get_by_name(name) — return a contact or None
  - .add_contact(data) — store a new contact
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Contact:
    """Single contact entry compatible with pyMC_core handler expectations."""

    name: str
    public_key: str  # 64-char hex string


class ContactBook:
    """In-memory contact book that satisfies the pyMC_core contacts interface."""

    def __init__(self) -> None:
        self.contacts: list[Contact] = []

    def get_by_name(self, name: str) -> Contact | None:
        for contact in self.contacts:
            if contact.name == name:
                return contact
        return None

    def add_contact(self, data: dict[str, str] | Contact) -> None:
        if isinstance(data, Contact):
            entry = data
        else:
            entry = Contact(name=data.get("name", ""), public_key=data.get("public_key", ""))
        if not entry.name or not entry.public_key:
            return
        # Update existing or append
        for i, existing in enumerate(self.contacts):
            if existing.name == entry.name:
                self.contacts[i] = entry
                return
        self.contacts.append(entry)
