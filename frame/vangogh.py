"""Curated public-domain Van Gogh portrait images for frame art mode."""

from __future__ import annotations

PAINTINGS = [
    {
        "key": "self_portrait_felt_hat",
        "title": "Self-Portrait with Grey Felt Hat",
        "year": "1887",
        "url": "https://upload.wikimedia.org/wikipedia/commons/6/6b/SelbstPortrait_VG2.jpg",
    },
    {
        "key": "dr_gachet",
        "title": "Portrait of Dr. Gachet",
        "year": "1890",
        "url": "https://upload.wikimedia.org/wikipedia/commons/4/4c/Van_Gogh_-_Bildnis_Doctor_Gachet_mit_Pfeife.jpeg",
    },
    {
        "key": "madame_ginoux",
        "title": "L'Arlésienne: Madame Ginoux",
        "year": "1890",
        "url": "https://upload.wikimedia.org/wikipedia/commons/d/da/Vincent_van_Gogh_-_L'Arlesienne_(Madame_Ginoux).jpg",
    },
]


def painting_by_key(key: str):
    for painting in PAINTINGS:
        if painting["key"] == key:
            return painting
    return PAINTINGS[0]