"""
SAP2000 colour conversion utilities for Rhino export.

Converts SAP2000 colour values (integer codes or named strings) into
``System.Drawing.Color`` objects suitable for Rhino layers and object
attributes.

All functions are safe to call from standard Python (outside Rhino) —
they return ``None`` or a dict representation if the Rhino colour API
is unavailable.
"""

import typing as t

# ── Lazily-resolved Rhino colour API ─────────────────────────────────────

_Color = None  # System.Drawing.Color


def _ensure_color():
    """Import ``System.Drawing.Color`` — only works inside Rhino (IronPython)."""
    global _Color
    if _Color is None:
        try:
            import System.Drawing
            _Color = System.Drawing.Color
        except ImportError:
            _Color = False  # Sentinel: not available


def safe_str(value: t.Any) -> str:
    """Safely convert value to string."""
    if value is None:
        return ""
    return str(value)


def color_from_name(color_value: t.Any):
    """Convert a SAP2000 colour value to a ``System.Drawing.Color``.

    Accepts integer colour codes (SAP2000's 0xRRGGBB format or predefined
    constants) and named colours (``'Red'``, ``'Gray8Dark'``, etc.).

    Returns ``System.Drawing.Color`` when running inside Rhino, or a
    ``(r, g, b)`` tuple when running in standard Python.
    """
    _ensure_color()
    if color_value is None:
        return None

    if isinstance(color_value, (int, float)):
        color_str = str(color_value)
    else:
        color_str = safe_str(color_value).strip()

    if not color_str:
        return None

    # ── 1. Integer colour code ─────────────────────────────────────────
    try:
        color_int = int(float(color_str))

        # Predefined SAP2000 colour map
        if _Color:
            sap_map = {
                13107400: _Color.FromArgb(200, 200, 200),
                16639:    _Color.FromArgb(0, 65, 255),
                43775:    _Color.FromArgb(0, 170, 255),
                54527:    _Color.FromArgb(0, 212, 255),
                9498256:  _Color.FromArgb(144, 144, 144),
                16711680: _Color.Red,
                65280:    _Color.Green,
                255:      _Color.Blue,
                65535:    _Color.Cyan,
                16711935: _Color.Magenta,
                16776960: _Color.Yellow,
                16777215: _Color.White,
                0:        _Color.Black,
                8421504:  _Color.Gray,
                12632256: _Color.LightGray,
            }
        else:
            sap_map = {
                13107400: (200, 200, 200),
                16639:    (0, 65, 255),
                43775:    (0, 170, 255),
                54527:    (0, 212, 255),
                9498256:  (144, 144, 144),
                16711680: (255, 0, 0),
                65280:    (0, 255, 0),
                255:      (0, 0, 255),
                65535:    (0, 255, 255),
                16711935: (255, 0, 255),
                16776960: (255, 255, 0),
                16777215: (255, 255, 255),
                0:        (0, 0, 0),
                8421504:  (128, 128, 128),
                12632256: (192, 192, 192),
            }

        if color_int in sap_map:
            return sap_map[color_int]

        # Extract RGB from 0xRRGGBB
        if 0 <= color_int <= 0xFFFFFF:
            r = (color_int >> 16) & 0xFF
            g = (color_int >> 8) & 0xFF
            b = color_int & 0xFF
            if _Color:
                return _Color.FromArgb(r, g, b)
            return (r, g, b)

    except (ValueError, TypeError):
        pass

    # ── 2. Named colours ───────────────────────────────────────────────
    # Build fallback name → RGB map
    fallback_names = {
        "red":     (255, 0, 0),
        "green":   (0, 128, 0),
        "blue":    (0, 0, 255),
        "yellow":  (255, 255, 0),
        "cyan":    (0, 255, 255),
        "magenta": (255, 0, 255),
        "orange":  (255, 165, 0),
        "purple":  (128, 0, 128),
        "black":   (0, 0, 0),
        "white":   (255, 255, 255),
        "brown":   (165, 42, 42),
        "pink":    (255, 192, 203),
        "gold":    (255, 215, 0),
        "silver":  (192, 192, 192),
        "gray":    (128, 128, 128),
        "grey":    (128, 128, 128),
    }

    color_lower = color_str.lower()

    if _Color:
        sap_color_names = {
            "Gray8Dark": _Color.FromArgb(20, 20, 20),
            "Gray7Dark": _Color.FromArgb(40, 40, 40),
            "Gray6Dark": _Color.FromArgb(60, 60, 60),
            "Gray5Dark": _Color.FromArgb(80, 80, 80),
            "Gray4Dark": _Color.FromArgb(100, 100, 100),
            "Gray3Dark": _Color.FromArgb(120, 120, 120),
            "Gray2Dark": _Color.FromArgb(140, 140, 140),
            "Gray1Dark": _Color.FromArgb(160, 160, 160),
            "Gray8":     _Color.FromArgb(180, 180, 180),
            "Gray7":     _Color.FromArgb(200, 200, 200),
            "Gray6":     _Color.FromArgb(220, 220, 220),
            "Gray5":     _Color.FromArgb(230, 230, 230),
            "Gray4":     _Color.FromArgb(240, 240, 240),
            "Gray3":     _Color.FromArgb(245, 245, 245),
            "Gray2":     _Color.FromArgb(250, 250, 250),
            "Gray1":     _Color.FromArgb(255, 255, 255),
            "Red":       _Color.Red,
            "Green":     _Color.Green,
            "Blue":      _Color.Blue,
            "Yellow":    _Color.Yellow,
            "Cyan":      _Color.Cyan,
            "Magenta":   _Color.Magenta,
            "Orange":    _Color.Orange,
            "Purple":    _Color.Purple,
            "Black":     _Color.Black,
            "White":     _Color.White,
            "Brown":     _Color.Brown,
            "Pink":      _Color.Pink,
            "Gold":      _Color.Gold,
            "Silver":    _Color.Silver,
        }
        for key, value in sap_color_names.items():
            if key.lower() == color_lower:
                return value
    else:
        # Check exact match in fallback names
        if color_lower in fallback_names:
            return fallback_names[color_lower]

    # ── 3. Simple colour detection (fallback) ──────────────────────────
    if "grey" in color_lower or "gray" in color_lower:
        if "dark" in color_lower:
            return _Color.DarkGray if _Color else (64, 64, 64)
        elif "light" in color_lower:
            return _Color.LightGray if _Color else (211, 211, 211)
        return _Color.Gray if _Color else (128, 128, 128)

    for name, rgb in fallback_names.items():
        if name in color_lower:
            if _Color:
                return _Color.FromArgb(*rgb)
            return rgb

    # Ultimate fallback
    return _Color.Gray if _Color else (128, 128, 128)


def get_sap2000_color(color_value, default_color=None):
    """Get colour from a SAP2000 colour value with a sensible default.

    Args:
        color_value: SAP2000 colour value (int, str, or ``None``).
        default_color: Fallback — can be a ``System.Drawing.Color``,
            a colour name string, or ``None`` (returns gray).

    Returns:
        ``System.Drawing.Color`` (inside Rhino) or ``(r, g, b)`` tuple.
    """
    color = color_from_name(color_value)
    if color is not None:
        return color

    if default_color is not None:
        if isinstance(default_color, str):
            return color_from_name(default_color) or (128, 128, 128)
        return default_color

    return (128, 128, 128)


# ── Predefined colour schemes ────────────────────────────────────────────

RESTRAINT_COLORS = {
    "fully_fixed": (255, 0, 0),          # Red
    "pinned":      (0, 0, 255),          # Blue
    "roller":      (0, 128, 0),          # Green
    "free":        (211, 211, 211),      # LightGray
    "constrained": (128, 0, 128),        # Purple
}

SHELL_PALETTE = [
    (220, 220, 255),   # Light Blue
    (220, 255, 220),   # Light Green
    (255, 220, 220),   # Light Red
    (255, 255, 220),   # Light Yellow
    (220, 255, 255),   # Light Cyan
    (255, 220, 255),   # Light Magenta
]

FRAME_PALETTE = [
    (200, 200, 255),   # Light Blue
    (255, 200, 200),   # Light Red
    (200, 255, 200),   # Light Green
    (255, 255, 200),   # Light Yellow
    (200, 255, 255),   # Light Cyan
    (255, 200, 255),   # Light Magenta
]
