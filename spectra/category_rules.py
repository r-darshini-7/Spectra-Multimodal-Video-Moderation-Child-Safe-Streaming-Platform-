"""Shared age-tier category options and validation for video metadata."""

from __future__ import annotations

AGE_GROUPS = {"preschool", "kids", "tweens", "teens", "unrestricted"}

CATEGORY_OPTIONS = {
    "preschool": (
        "Nursery Rhymes",
        "Moral Stories",
        "Cartoons",
        "Alphabet & Phonics",
        "Numbers & Counting",
        "Colors & Shapes",
        "Animal Videos",
        "Songs",
        "Basic Learning",
        "Simple Stories",
    ),
    "kids": (
        "Educational Videos",
        "Animated Stories",
        "Cartoons",
        "Science Basics",
        "Mathematics",
        "Crafts/DIY",
        "Nature & Animals",
        "Moral Stories",
        "Guided Learning",
        "Simple Experiments",
    ),
    "tweens": (
        "Coding Tutorials",
        "Technology Videos",
        "Educational Content",
        "Music",
        "Vlogs",
        "Gaming",
        "Sports",
        "Science Experiments",
        "Reviews",
        "Creative Content",
    ),
    "teens": (
        "Gaming",
        "Technology",
        "Music",
        "Vlogs",
        "Reviews",
        "Commentary",
        "Sports",
        "Entertainment",
        "Educational Videos",
        "Career Guidance",
        "Current-Topic Discussions",
    ),
    "unrestricted": (
        "Entertainment",
        "Gaming",
        "Music",
        "Vlogs",
        "News",
        "Documentaries",
        "Tutorials",
        "Reviews",
        "Commentary",
        "Educational Content",
    ),
}

_ALLOWED_CATEGORY_NAMES = {
    age_group: {category.casefold() for category in categories}
    for age_group, categories in CATEGORY_OPTIONS.items()
}


def category_is_allowed(age_group: str, category: str) -> bool:
    """Return whether a category is permitted for the selected age tier."""
    if age_group not in AGE_GROUPS:
        return False
    normalized_category = " ".join(category.casefold().split())
    return normalized_category in _ALLOWED_CATEGORY_NAMES[age_group]
