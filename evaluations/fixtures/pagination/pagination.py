"""Intentionally incomplete pagination for the implementation benchmark."""


def page(items, offset=0, limit=20):
    return items[offset:limit]
