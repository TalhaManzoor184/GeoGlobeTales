"""Visual theme ("night atlas"). Episodes can override any key via "style": {...}."""

STYLE = {
    "ocean": "#071726",
    "land": "#294b66",
    "border": "#8aa5bc",
    "graticule": "#15324d",
    "river": "#4a90c8",
    "space_top": "#02060d",
    "space_bottom": "#0b1a2e",
    "atmosphere": "#5ab4ff",
    "text": "#ffffff",
    "accent": "#ffd400",
    "stroke": "#06121f",
    "panel": "#06121fcc",
}

# Named colours usable anywhere a colour is expected in an episode file.
PALETTE = {
    "yellow": "#ffd400",
    "green": "#1fb45a",
    "blue": "#3d8bff",
    "red": "#ff4d4d",
    "orange": "#ff9f1c",
    "purple": "#a66cff",
    "teal": "#14c8b4",
    "white": "#ffffff",
    "grey": "#8aa0b4",
}


def color(c: str) -> str:
    return PALETTE.get(c, c)


def hex_rgb(c: str) -> tuple[float, float, float]:
    c = color(c).lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def hex_rgba255(c: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    c = color(c).lstrip("#")
    a = int(c[6:8], 16) / 255.0 if len(c) == 8 else 1.0
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), int(round(255 * a * alpha)))
