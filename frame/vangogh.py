"""Curated public-domain Van Gogh portrait images for frame art mode."""

from __future__ import annotations

PAINTINGS = [

]


def painting_by_key(key: str):
    for painting in PAINTINGS:
        if painting["key"] == key:
            return painting
    return PAINTINGS[0]