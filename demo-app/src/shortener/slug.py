"""Slug helper."""


def slugify(s: str) -> str:
    return s.lower().replace(" ", "-")
