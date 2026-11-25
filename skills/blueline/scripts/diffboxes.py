"""Find changed regions between before/after slide PNGs.
Usage: diffboxes.py IMGDIR before after [before after ...]
Prints JSON {"after": [[x, y, w, h, cells], ...]}: each box in % of the image, plus how
many grid cells changed inside it (small counts are specks). data.json takes [x, y, w, h]."""
import sys, json
from PIL import Image, ImageChops, ImageFilter

GW, GH = 320, 180          # diff grid (4px cells at 1280x720)
THRESH = 36                # per-cell max channel difference that counts as a change
DILATE = 3                 # cells; merges nearby changes into one box
PAD = 0.6                  # % padding around each box
MIN_CELLS = 3

def boxes(img_dir, b, a):
    A = Image.open(f"{img_dir}/{b}.png").convert("RGB")
    B = Image.open(f"{img_dir}/{a}.png").convert("RGB")
    if A.size != B.size:
        B = B.resize(A.size, Image.LANCZOS)
    d = ImageChops.difference(A, B).convert("L")
    # max-pool to the grid so thin strokes survive
    d = d.filter(ImageFilter.MaxFilter(3)).resize((GW, GH), Image.BOX)
    px = d.load()
    mask = [[px[x, y] > THRESH for x in range(GW)] for y in range(GH)]
    # dilate
    dil = [[False] * GW for _ in range(GH)]
    for y in range(GH):
        for x in range(GW):
            if mask[y][x]:
                for yy in range(max(0, y - DILATE), min(GH, y + DILATE + 1)):
                    row = dil[yy]
                    for xx in range(max(0, x - DILATE), min(GW, x + DILATE + 1)):
                        row[xx] = True
    # connected components on the dilated mask, boxed by the undilated cells inside
    seen = [[False] * GW for _ in range(GH)]
    out = []
    for y in range(GH):
        for x in range(GW):
            if dil[y][x] and not seen[y][x]:
                stack = [(x, y)]; seen[y][x] = True
                x0 = y0 = 10**9; x1 = y1 = -1; n = 0
                while stack:
                    cx, cy = stack.pop()
                    if mask[cy][cx]:
                        n += 1
                        x0 = min(x0, cx); y0 = min(y0, cy); x1 = max(x1, cx); y1 = max(y1, cy)
                    for nx, ny in ((cx+1, cy), (cx-1, cy), (cx, cy+1), (cx, cy-1)):
                        if 0 <= nx < GW and 0 <= ny < GH and dil[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True; stack.append((nx, ny))
                if n >= MIN_CELLS:
                    out.append([x0, y0, x1 + 1, y1 + 1, n])
    res = []
    for x0, y0, x1, y1, n in out:
        bx = max(0, x0 / GW * 100 - PAD); by = max(0, y0 / GH * 100 - PAD * 16 / 9)
        bw = min(100, x1 / GW * 100 + PAD) - bx; bh = min(100, y1 / GH * 100 + PAD * 16 / 9) - by
        res.append([round(bx, 1), round(by, 1), round(bw, 1), round(bh, 1), n])
    res.sort(key=lambda r: (r[1], r[0]))
    return res

def main():
    if len(sys.argv) < 4 or len(sys.argv[2:]) % 2:
        sys.exit(__doc__)
    d = sys.argv[1]; args = sys.argv[2:]
    result = {}
    for i in range(0, len(args), 2):
        result[args[i + 1]] = boxes(d, args[i], args[i + 1])
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
