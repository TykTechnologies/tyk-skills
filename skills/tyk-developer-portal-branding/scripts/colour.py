#!/usr/bin/env python3
"""Colour arithmetic for the brand kit: WCAG 2.x contrast and a few safe derivations.

Contrast ratio and relative luminance follow the WCAG 2.2 definitions
(https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio). 4.5:1 is the AA minimum for body text,
3:1 for large text (24px, or 18.66px bold) and for user-interface components.
"""

from __future__ import annotations

import re

HEX = re.compile(r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})")
AA_TEXT = 4.5
AA_LARGE = 3.0
WHITE = "#FFFFFF"
INK = "#14161A"


def parse(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError(f"not a #rgb or #rrggbb colour: {value!r}")
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(channel))):02X}" for channel in rgb)


def normalise(value: str) -> str:
    return to_hex(parse(value))


def luminance(value: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = parse(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def is_dark(value: str) -> bool:
    """True when white text reads better on this colour than dark text does."""
    return contrast(value, WHITE) >= contrast(value, INK)


def on(background: str) -> str:
    """White or near-black, whichever reads better on the background."""
    return WHITE if is_dark(background) else INK


def mix(a: str, b: str, share_of_b: float) -> str:
    ra, ga, ba = parse(a)
    rb, gb, bb = parse(b)
    return to_hex((ra + (rb - ra) * share_of_b, ga + (gb - ga) * share_of_b, ba + (bb - ba) * share_of_b))


def darken(value: str, share: float) -> str:
    return mix(value, "#000000", share)


def readable(value: str, background: str = WHITE, minimum: float = AA_TEXT) -> str:
    """The colour itself when it meets `minimum` on the background, else the nearest darker
    (on a light background) or lighter (on a dark one) step of it that does."""
    if contrast(value, background) >= minimum:
        return normalise(value)
    target = "#000000" if not is_dark(background) else WHITE
    for step in range(1, 21):
        candidate = mix(value, target, step / 20)
        if contrast(candidate, background) >= minimum:
            return candidate
    return target
