"""Draws the FiberView icon: navy rounded tile with a bright green calcium transient.
python install/make_icon.py  ->  assets/fiberview_512.png, _256.png, .ico, .icns"""
import math, os
from PIL import Image, ImageDraw, ImageFilter

S = 2048                                    # draw large, downsample for smooth edges
TOP, BOT = (0x10, 0x1d, 0x3d), (0x06, 0x0b, 0x1a)
here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, "..", "assets")
os.makedirs(out, exist_ok=True)

grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
for y in range(S):
    f = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * f) for a, b in zip(TOP, BOT)) + (255,))
m = round(S * 0.04)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=round(S * 0.205), fill=255)
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
tile.paste(grad, (0, 0), mask)
# thin lighter rim
rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(rim).rounded_rectangle([m, m, S - m, S - m], radius=round(S * 0.205),
                                      outline=(70, 100, 160, 90), width=round(S * 0.006))
tile = Image.alpha_composite(tile, rim)

# calcium transient: flat baseline, fast rise, exponential decay
x0, x1, base, peak = 0.16, 0.84, 0.66, 0.27
xr, rise = 0.38, 0.035
pts = []
for i in range(240):
    x = x0 + (x1 - x0) * i / 239
    if x < xr:
        y = base
    elif x < xr + rise:
        y = base - (base - peak) * (1 - math.exp(-(x - xr) / (rise / 3))) / (1 - math.exp(-3))
    else:
        y = base - (base - peak) * math.exp(-(x - xr - rise) / 0.11)
    pts.append((x * S, y * S))
w = round(S * 0.052)

def stroke(color, width):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = width / 2
    for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):      # round brush stamped densely along the path
        n = max(1, int(math.hypot(xb - xa, yb - ya) / (r * 0.15)))
        for k in range(n + 1):
            x, y = xa + (xb - xa) * k / n, ya + (yb - ya) * k / n
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return im

glow = stroke((70, 255, 130, 200), round(w * 2.4)).filter(ImageFilter.GaussianBlur(S * 0.03))
img = Image.alpha_composite(tile, glow)
img = Image.alpha_composite(img, stroke((120, 255, 160, 255), w))
img = Image.alpha_composite(img, stroke((225, 255, 232, 255), round(w * 0.38)))   # bright core
img = Image.composite(img, Image.new("RGBA", (S, S), (0, 0, 0, 0)), mask)

big = img.resize((512, 512), Image.LANCZOS)
big.save(os.path.join(out, "fiberview_512.png"))
img.resize((256, 256), Image.LANCZOS).save(os.path.join(out, "fiberview_256.png"))
img.resize((1024, 1024), Image.LANCZOS).save(os.path.join(out, "fiberview.ico"),
                                              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.resize((1024, 1024), Image.LANCZOS).save(os.path.join(out, "fiberview.icns"))
print("icons written to", os.path.abspath(out))
