"""Generate the multi-resolution ScholarSprout Windows icon."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


SIZE = 1024
SCALE = SIZE / 64
HERE = Path(__file__).resolve().parent


def _mix(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * amount) for a, b in zip(left, right))


def _stops(value: float, stops: list[tuple[float, tuple[int, int, int]]]) -> tuple[int, int, int]:
    if value <= stops[0][0]:
        return stops[0][1]
    for (start, start_color), (end, end_color) in zip(stops, stops[1:]):
        if value <= end:
            return _mix(start_color, end_color, (value - start) / (end - start))
    return stops[-1][1]


def _point(x: float, y: float) -> tuple[int, int]:
    return round(x * SCALE), round(y * SCALE)


def _cubic(
    start: tuple[float, float],
    control_a: tuple[float, float],
    control_b: tuple[float, float],
    end: tuple[float, float],
    steps: int = 80,
) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        x = u**3 * start[0] + 3 * u**2 * t * control_a[0] + 3 * u * t**2 * control_b[0] + t**3 * end[0]
        y = u**3 * start[1] + 3 * u**2 * t * control_a[1] + 3 * u * t**2 * control_b[1] + t**3 * end[1]
        points.append(_point(x, y))
    return points


def _gradient_fill(mask: Image.Image, start: tuple[int, int, int], end: tuple[int, int, int], horizontal: bool) -> Image.Image:
    gradient = Image.new("RGBA", (SIZE, SIZE))
    pixels = gradient.load()
    for y in range(SIZE):
        for x in range(SIZE):
            amount = (x if horizontal else y) / (SIZE - 1)
            color = _mix(start, end, amount)
            pixels[x, y] = (*color, 255)
    gradient.putalpha(mask)
    return gradient


def build_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    rounded = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(rounded).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=round(18 * SCALE), fill=255)

    background = Image.new("RGBA", (SIZE, SIZE))
    pixels = background.load()
    center_x, center_y, radius = 49 * SCALE, 13 * SCALE, 58 * SCALE
    radial_stops = [
        (0.0, (102, 245, 214)),
        (0.34, (34, 90, 85)),
        (0.72, (16, 40, 42)),
        (1.0, (7, 18, 15)),
    ]
    for y in range(SIZE):
        for x in range(SIZE):
            distance = math.hypot(x - center_x, y - center_y) / radius
            pixels[x, y] = (*_stops(distance, radial_stops), 255)
    background.putalpha(rounded)
    canvas.alpha_composite(background)

    ground = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ground_points = _cubic((8, 53), (21, 48), (41, 47), (56, 52))
    ImageDraw.Draw(ground).line(ground_points, fill=(184, 255, 241, 51), width=round(2.4 * SCALE), joint="curve")
    canvas.alpha_composite(ground)

    stem_points = _cubic((27, 50), (32, 45), (32, 39), (34, 33))[:-1]
    stem_points += _cubic((34, 33), (36, 24), (42, 17), (49, 12))[1:]
    stem_width = round(4.8 * SCALE)
    stem_mask = Image.new("L", (SIZE, SIZE), 0)
    stem_draw = ImageDraw.Draw(stem_mask)
    stem_draw.line(stem_points, fill=255, width=stem_width, joint="curve")
    stem_radius = stem_width // 2
    for x, y in (stem_points[0], stem_points[-1]):
        stem_draw.ellipse((x - stem_radius, y - stem_radius, x + stem_radius, y + stem_radius), fill=255)
    canvas.alpha_composite(_gradient_fill(stem_mask, (168, 187, 255), (102, 245, 214), horizontal=False))

    left_mask = Image.new("L", (SIZE, SIZE), 0)
    left_points = _cubic((34, 35), (26, 34), (20, 29), (18, 21))[:-1]
    left_points += _cubic((18, 21), (26, 21), (33, 26), (34, 35))[1:]
    ImageDraw.Draw(left_mask).polygon(left_points, fill=255)
    canvas.alpha_composite(_gradient_fill(left_mask, (200, 255, 244), (114, 234, 213), horizontal=True))

    right_mask = Image.new("L", (SIZE, SIZE), 0)
    right_points = _cubic((35, 40), (38, 33), (44, 30), (51, 31))[:-1]
    right_points += _cubic((51, 31), (48, 37), (42, 40), (35, 40))[1:]
    ImageDraw.Draw(right_mask).polygon(right_points, fill=255)
    canvas.alpha_composite(_gradient_fill(right_mask, (85, 223, 198), (200, 255, 244), horizontal=True))

    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((*_point(42, 5), *_point(56, 19)), fill=(168, 187, 255, 128))
    glow = glow.filter(ImageFilter.GaussianBlur(round(2.5 * SCALE)))
    canvas.alpha_composite(glow)

    star = ImageDraw.Draw(canvas)
    star.ellipse((*_point(44.8, 7.8), *_point(53.2, 16.2)), fill=(234, 255, 247, 255))
    star.line((*_point(49, 5.5), *_point(49, 18.5)), fill=(223, 255, 248, 140), width=round(1.2 * SCALE))
    star.line((*_point(42.5, 12), *_point(55.5, 12)), fill=(223, 255, 248, 140), width=round(1.2 * SCALE))
    return canvas


def main() -> None:
    icon = build_icon()
    output_dir = HERE / "output"
    output_dir.mkdir(exist_ok=True)
    icon.save(output_dir / "scholarsprout-icon-preview.png")
    icon.save(
        HERE / "scholarsprout.ico",
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (256, 256)],
    )


if __name__ == "__main__":
    main()
