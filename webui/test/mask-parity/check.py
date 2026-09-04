"""Compare the TS port in prepare.ts against Pillow, the thing it ports."""
import json, pathlib, sys
from PIL import Image, ImageFilter

here = pathlib.Path(__file__).parent
W, H = 256, 192

union = Image.frombytes("L", (W, H), (here / "union.raw").read_bytes())

def diff(name, produced):
    got = Image.frombytes("L", (W, H), (here / name).read_bytes())
    a = produced.tobytes()
    b = got.tobytes()
    deltas = [abs(x - y) for x, y in zip(a, b)]
    worst = max(deltas)
    mean = sum(deltas) / len(deltas)
    off = sum(1 for d in deltas if d > 1)
    print(f"{name:16s} max={worst:3d}  mean={mean:6.4f}  pixels>1={off:6d} / {len(deltas)}")
    return worst

print("-- MaxFilter (grow) --")
worst_dilate = 0
for grow in (0, 3, 8, 32):
    ref = union if grow == 0 else union.filter(ImageFilter.MaxFilter(grow * 2 + 1))
    worst_dilate = max(worst_dilate, diff(f"dilate_{grow}.raw", ref))

print("-- GaussianBlur --")
worst_blur = 0
for blur in (1, 4, 8, 32):
    ref = union.filter(ImageFilter.GaussianBlur(blur))
    worst_blur = max(worst_blur, diff(f"blur_{blur}.raw", ref))

print("-- targetSize --")
sizes = json.loads((here / "sizes.json").read_text())
cases = [(3000, 2000), (1024, 1536), (1000, 700), (50, 40), (4096, 4096), (1919, 1081)]
ok = True
for (w, h), got in zip(cases, sizes):
    scale = min(1.0, 2048 / max(w, h))
    w2 = max(64, int(w * scale) // 16 * 16)
    h2 = max(64, int(h * scale) // 16 * 16)
    match = (w2, h2) == (got["width"], got["height"])
    ok &= match
    print(f"  {w}x{h:<5} python={w2}x{h2:<10} ts={got['width']}x{got['height']:<10} {'OK' if match else 'MISMATCH'}")

print()
print(f"dilate worst delta : {worst_dilate}")
print(f"blur   worst delta : {worst_blur}")
print(f"snap-to-16         : {'exact' if ok else 'MISMATCH'}")
sys.exit(0 if (worst_dilate == 0 and ok) else 1)
