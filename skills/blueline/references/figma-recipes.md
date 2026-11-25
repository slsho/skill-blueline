# Figma Slides recipes

Plugin API snippets for `use_figma` on a Slides file. Always `return` what you need
(console output isn't returned). Load every font a text node uses before editing it.

## Most changes don't need code

Text changes and property changes are `ops` in `data.json` (`replace`, `set`), and `figjs.py`
writes the code for the preview, the apply and the undo. Write code only for what those can't
say: new elements, layout. Put it in `<review-dir>/apply/<card>.js` and point an op at it:
`{"op": "js", "file": "apply/4.js"}`. The file is the body of `async (k) => { ... }`. Change
nodes only through `k`, so the same code runs on a preview clone and can be undone:

- `await k.node(id)`: the node (on a preview, its copy on the clone). Never `figma.getNodeByIdAsync`.
- `await k.text(id, find, repl)`: replace words, keep their style, refuse if they don't fit.
- `await k.set(id, { prop: value })`, `await k.resize(id, w, h)`: remembered for undo.
- `k.made(node)`: register a node you created; undo removes it.

```js
// apply/4.js: a TEMPLATE tag on the slide that holds node 3:14
const slide = await k.node("3:14");
const tag = k.made(figma.createFrame());
slide.appendChild(tag);                      // appendChild before x/y
tag.layoutMode = "HORIZONTAL"; tag.primaryAxisSizingMode = "AUTO"; tag.counterAxisSizingMode = "AUTO";
/* ...style it as in "Add a small element" below... */
tag.x = slide.width - tag.width - 72; tag.y = 60;
```

The snippets below are the Plugin API pieces to build those files from.

## Slides in order, and the slide a node sits on

```js
const grid = figma.currentPage.children.find(c => c.type === "SLIDE_GRID");
const slides = grid.children.flatMap(row => row.children);          // slide 1 = slides[0]
const slideOf = async id => { let n = await figma.getNodeByIdAsync(id); while (n && n.type !== "SLIDE") n = n.parent; return n; };
return slides.map((s, i) => ({ n: i + 1, id: s.id, skipped: s.isSkippedSlide }));
```

## Load the fonts in a text node

```js
async function loadFonts(t) {
  const fonts = t.fontName === figma.mixed ? t.getStyledTextSegments(["fontName"]).map(s => s.fontName) : [t.fontName];
  await Promise.all(fonts.map(f => figma.loadFontAsync(f)));
}
```

## Replace a phrase and keep its style

Finds `find` in any text node on the slide, inserts the new words with the style of the
text they replace, then removes the old words. Nothing else in the node changes.

```js
async function replaceOnSlide(slide, find, repl) {
  for (const t of slide.findAllWithCriteria({ types: ["TEXT"] })) {
    const i = t.characters.indexOf(find);
    if (i < 0) continue;
    await loadFonts(t);
    t.insertCharacters(i + find.length, repl, "BEFORE");   // takes the style of the last replaced character
    t.deleteCharacters(i, i + find.length);
    return t.id;
  }
  return null;   // report it as failed, don't guess
}
```

For a capitalised word mid-sentence (`Problems` -> `In our experience, problems`), replace
the whole phrase, not just the added part.

## Add a line under existing text, in another colour

```js
await loadFonts(t);
const start = t.characters.length;
t.insertCharacters(start, "\n" + line, "BEFORE");
const end = t.characters.length;
await figma.loadFontAsync({ family: "Inter", style: "Regular" });
t.setRangeFontName(start + 1, end, { family: "Inter", style: "Regular" });
t.setRangeFills(start + 1, end, [{ type: "SOLID", color: { r: 0.0, g: 0.52, b: 0.55 } }]);  // use the deck's own colour
```

Re-space the rows afterwards if the block now runs into the footer; check with a screenshot.

## Centre numbers inside circles

Only numbers that sit on a circle: the text box must match an `ELLIPSE` beside it. Footer page
numbers are also one- or two-digit text, so a plain "is it a number" test would move them too.

```js
const undo = []; let n = 0;
for (const s of slides) for (const t of s.findAllWithCriteria({ types: ["TEXT"] })) {
  if (!/^(\d{1,2}|!)$/.test(t.characters.trim())) continue;
  const circle = t.parent && t.parent.children.find(c => c.type === "ELLIPSE" &&
    Math.abs(c.x - t.x) <= 1 && Math.abs(c.y - t.y) <= 1 && Math.abs(c.width - t.width) <= 1);
  if (!circle) continue;                                   // a page number, not a circle
  await loadFonts(t);
  undo.push({ op: "align", id: t.id, h: t.textAlignHorizontal, v: t.textAlignVertical });
  t.textAlignHorizontal = "CENTER"; t.textAlignVertical = "CENTER"; n++;
}
return { n, undo };
```

Count what you changed and put the real count in the card. Don't trust a count from an earlier draft.

## Add a small element (tag, line, shape)

`appendChild` first, then set `x`/`y`, at every level. Setting position before parenting
lands the node 240px off.

```js
const tag = figma.createFrame();
slide.appendChild(tag);
tag.layoutMode = "HORIZONTAL"; tag.primaryAxisSizingMode = "AUTO"; tag.counterAxisSizingMode = "AUTO";
tag.paddingLeft = tag.paddingRight = 16; tag.paddingTop = tag.paddingBottom = 6;
tag.cornerRadius = 999; tag.fills = []; tag.strokes = [{ type: "SOLID", color: { r: .55, g: .58, b: .62 } }]; tag.strokeWeight = 2;
await figma.loadFontAsync({ family: "Inter", style: "Bold" });
const label = figma.createText();
tag.appendChild(label);
label.fontName = { family: "Inter", style: "Bold" }; label.fontSize = 20; label.characters = "TEMPLATE";
label.letterSpacing = { unit: "PERCENT", value: 8 }; label.fills = tag.strokes;
tag.x = slide.width - tag.width - 72; tag.y = 60;
```

To put something behind existing layers: `slide.insertChild(0, node)` instead of `appendChild`.

## Hide a slide instead of deleting it

```js
slides[4].isSkippedSlide = true;   // slide 5 stays in the file, out of the presentation
```

## Check the text still fits its box

Text boxes in imported decks are fixed size (`textAutoResize = "NONE"`), so longer text can spill
out of its card without any error. Measure a throwaway copy that grows to fit:

```js
async function fits(t) {
  const c = t.clone(); t.parent.appendChild(c); await loadFonts(c); c.textAutoResize = "HEIGHT";
  const need = c.height; c.remove(); return { need, box: t.height, ok: need <= t.height + 1 };
}
```

`k.text` and `replace` ops run this check for you and refuse the card if it doesn't fit. Run it
yourself only for text you change some other way. If it doesn't fit, say so on the card (or in
`failed`) instead of shrinking the font on your own.

## Preview a change without touching the slide

`figjs.py <review-dir> preview <ids>` does this from the cards' ops: it clones each slide (the clone
goes in front of the original), applies the cards to the clone and returns the clone ids. Remove
them with `figjs.py <review-dir> drop <ids>` in a later call: a slide removed in the same call that
cloned it comes back.

Keep the ids of the clones you make and remove exactly those ids. Don't mark clones by name: Figma
renames slides to their position number, so a name you set is gone by the next call. Only remove
clones you created for a preview.

Use the same edit code for the preview and for the real change, so what the owner approves is what
lands. Have each edit return how to undo it (old text, old alignment, ids of nodes you created,
old `isSkippedSlide`), and keep that list until the owner is happy.
