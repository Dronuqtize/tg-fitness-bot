from __future__ import annotations


def parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    ids = set()
    for part in value.replace(",", " ").split():
        try:
            ids.add(int(part.strip()))
        except ValueError:
            continue
    return ids
