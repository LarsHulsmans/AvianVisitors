"""Curated public-domain Van Gogh portrait images for frame art mode."""

from __future__ import annotations

PAINTINGS = [
    {
        "key": "self_portrait_felt_hat",
        "title": "Self-Portrait with Grey Felt Hat",
        "year": "1887",
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Vincent%20van%20Gogh%2C%20Self-portrait%2C%201887%2C%20Van%20Gogh%20Museum%2C%20Amsterdam.jpg?width=1600",
    },
    {
        "key": "dr_gachet",
        "title": "Portrait of Dr. Gachet",
        "year": "1890",
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Vincent%20van%20Gogh%20-%20Portret%20van%20Dr.%20Gachet.jpg?width=1600",
    },
    {
        "key": "madame_ginoux",
        "title": "L'Arlésienne: Madame Ginoux",
        "year": "1890",
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Vincent%20van%20Gogh%2C%20L%27Arlesienne%20(Madame%20Ginoux)%2C%201890%2C%20Metropolitan%20Museum%20of%20Art.jpg?width=1600",
    },
]


def painting_by_key(key: str):
    for painting in PAINTINGS:
        if painting["key"] == key:
            return painting
    return PAINTINGS[0]