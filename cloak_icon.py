"""Иконка «плащ с капюшоном» (Pillow). Запуск напрямую создаёт app.ico для сборки exe."""
import math
import sys

BRAND = "#6c7cff"
STATUS_COLORS = {          # цвет плаща в трее
    "gray": "#8a94a6",
    "orange": "#f0a030",
    "green": "#00ee5c",
    "red": "#e5484d",
}


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c, other, t):
    return tuple(int(c[i] * (1 - t) + other[i] * t) for i in range(3))


def draw_icon(color=BRAND, size=256, bg=True):
    """Возвращает PIL.Image RGBA size×size."""
    from PIL import Image, ImageDraw

    S = 4
    N = 256 * S
    im = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(pts):
        return [(x * S, y * S) for x, y in pts]

    base = _rgb(color)
    light = _mix(base, (255, 255, 255), 0.22)
    dark = _mix(base, (0, 0, 0), 0.38)
    eye = _mix(base, (255, 255, 255), 0.7)

    if bg:
        d.rounded_rectangle([8 * S, 8 * S, 248 * S, 248 * S], radius=56 * S, fill=(20, 27, 45, 255))

    # подол с «зубцами»
    def hem_y(x):
        return 214 + 7 * abs(math.sin(3 * math.pi * (x - 46) / 164))

    hem = [(x, hem_y(x)) for x in range(46, 211, 4)]
    body = [(100, 112), (70, 150), (46, 214)] + hem + [(210, 214), (186, 150), (156, 112)]
    d.polygon(P(body), fill=base + (255,))

    # тень и складки рисуем на отдельном слое и накладываем с прозрачностью
    ov = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    right_hem = [(x, hem_y(x)) for x in range(128, 211, 4)]
    shade = [(128, 118), (156, 112), (186, 150), (210, 214)] + right_hem[::-1]
    od.polygon(P(shade), fill=dark + (110,))
    od.line(P([(112, 134), (98, 212)]), fill=dark + (200,), width=3 * S)
    od.line(P([(146, 134), (160, 212)]), fill=dark + (200,), width=3 * S)
    im = Image.alpha_composite(im, ov)
    d = ImageDraw.Draw(im)

    # капюшон
    d.ellipse(P([(80, 34), (176, 142)]), fill=light + (255,), outline=dark + (255,), width=3 * S)
    # лицо в тени
    d.ellipse(P([(101, 62), (155, 130)]), fill=(10, 14, 26, 255))
    # глаза
    for ex in (118, 138):
        d.ellipse(P([(ex - 4, 93), (ex + 4, 103)]), fill=eye + (255,))

    # застёжка
    d.ellipse(P([(119, 125), (137, 143)]), fill=(232, 190, 80, 255), outline=(150, 110, 30, 255), width=2 * S)

    return im.resize((size, size), Image.LANCZOS)


def draw_tray_icon(color, size=64):
    """Иконка для трея: без тёмной плашки и с обрезкой по фигуре — читается крупнее."""
    from PIL import Image

    big = draw_icon(color, 512, bg=False)
    fig = big.crop((80, 56, 432, 456))          # границы фигуры плаща (в координатах 512)
    side = max(fig.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(fig, ((side - fig.width) // 2, (side - fig.height) // 2), fig)
    return canvas.resize((size, size), Image.LANCZOS)


def save_ico(path="app.ico"):
    img = draw_icon(BRAND, 256)
    img.save(path, format="ICO",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "app.ico"
    save_ico(out)
    print("Создано:", out)
