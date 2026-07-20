"""Generate the original Agent IQ Microsoft 365 app icon set."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "packages" / "cowork" / "assets"
SCALE: Final = 4
AZURE: Final = "#0F6CBD"
AZURE_DARK: Final = "#0B4F8A"
AZURE_LIGHT: Final = "#1686C9"
CYAN: Final = "#66D9EF"
WHITE: Final = "#FFFFFF"


def _points(values: list[tuple[float, float]]) -> list[tuple[int, int]]:
    return [(round(x * SCALE), round(y * SCALE)) for x, y in values]


def _box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    return (
        round(values[0] * SCALE),
        round(values[1] * SCALE),
        round(values[2] * SCALE),
        round(values[3] * SCALE),
    )


def _downsample(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _a_mask(size: int = 192) -> Image.Image:
    mask = Image.new("L", (size * SCALE, size * SCALE), 0)
    draw = ImageDraw.Draw(mask)
    draw.polygon(
        _points(
            [
                (48, 144),
                (82, 48),
                (108, 48),
                (144, 144),
                (119, 144),
                (110, 119),
                (80, 119),
                (71, 144),
            ]
        ),
        fill=255,
    )
    draw.polygon(_points([(95, 75), (83, 105), (107, 105)]), fill=0)
    return mask


def _draw_network(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    nodes = ((95, 81), (87, 101), (103, 101))
    width = round(1.6 * SCALE)
    draw.line(_points([nodes[0], nodes[1]]), fill=WHITE, width=width)
    draw.line(_points([nodes[0], nodes[2]]), fill=WHITE, width=width)
    draw.line(_points([nodes[1], nodes[2]]), fill=WHITE, width=width)
    radius = 2.3
    for x, y in nodes:
        draw.ellipse(
            _box((x - radius, y - radius, x + radius, y + radius)),
            fill=WHITE,
        )


def generate_color_icon() -> Image.Image:
    """Create the full-bleed 192px store and flyout icon."""

    high_size = 192 * SCALE
    image = Image.new("RGBA", (high_size, high_size), AZURE)
    background = ImageDraw.Draw(image)
    background.polygon(
        _points([(104, 0), (192, 0), (192, 104)]),
        fill=AZURE_LIGHT,
    )
    background.polygon(
        _points([(0, 88), (0, 192), (112, 192)]),
        fill=AZURE_DARK,
    )
    mask = _a_mask()
    shadow_mask = Image.new("L", mask.size, 0)
    shadow_mask.paste(mask, (0, round(4 * SCALE)))
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(3.5 * SCALE))
    shadow = Image.new("RGBA", image.size, (0, 25, 55, 72))
    image.alpha_composite(Image.composite(shadow, Image.new("RGBA", image.size), shadow_mask))

    mark = Image.new("RGBA", image.size, WHITE)
    image.alpha_composite(Image.composite(mark, Image.new("RGBA", image.size), mask))

    facet_mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(facet_mask).polygon(
        _points([(95, 48), (108, 48), (144, 144), (119, 144)]),
        fill=238,
    )
    facet = Image.new("RGBA", image.size, CYAN)
    image.alpha_composite(Image.composite(facet, Image.new("RGBA", image.size), facet_mask))
    _draw_network(image)
    return _downsample(image, 192)


def generate_outline_icon() -> Image.Image:
    """Create the transparent white 32px app-bar rest icon."""

    image = Image.new("RGBA", (32 * SCALE, 32 * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = round(2.2 * SCALE)
    draw.line(
        _points([(6.5, 27), (15.6, 4.5), (25.5, 27)]),
        fill=WHITE,
        width=width,
        joint="curve",
    )
    draw.line(
        _points([(10.5, 19), (21.4, 19)]),
        fill=WHITE,
        width=width,
    )
    return _downsample(image, 32)


def generate_color32_icon() -> Image.Image:
    """Create the optional transparent full-color 32px pinned icon."""

    image = Image.new("RGBA", (32 * SCALE, 32 * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outer = _points(
        [
            (4.5, 28),
            (13.1, 3.5),
            (19.2, 3.5),
            (28, 28),
            (22, 28),
            (19.8, 21.7),
            (12.3, 21.7),
            (10.1, 28),
        ]
    )
    draw.polygon(outer, fill=AZURE)
    draw.polygon(_points([(16.1, 10), (13.3, 18), (18.9, 18)]), fill=(0, 0, 0, 0))
    draw.polygon(
        _points([(16, 3.5), (19.2, 3.5), (28, 28), (22, 28)]),
        fill=CYAN,
    )
    return _downsample(image, 32)


def generate(output: Path = DEFAULT_OUTPUT) -> tuple[Path, Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    paths = (
        output / "color.png",
        output / "outline.png",
        output / "color32.png",
    )
    images = (
        generate_color_icon(),
        generate_outline_icon(),
        generate_color32_icon(),
    )
    for image, path in zip(images, paths, strict=True):
        image.save(path, format="PNG", optimize=True)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    for path in generate(arguments.output):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
