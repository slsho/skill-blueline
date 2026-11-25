// Blueline runtime for use_figma on a Figma Slides file.
// figjs.py stores this once in the file (shared plugin data "blueline"/"lib"); every later call
// runs it with `new Function(lib)()`, so a call only carries the card's ops, not this code.
// Each card is applied as a whole or not at all, its undo records are kept in the file,
// and text that no longer fits its box is rolled back with a reason.
const NS = "blueline";
const fontsOf = t => t.fontName === figma.mixed ? t.getStyledTextSegments(["fontName"]).map(s => s.fontName) : [t.fontName];
const loadFonts = t => Promise.all(fontsOf(t).map(f => figma.loadFontAsync(f)));
const get = async id => { const n = await figma.getNodeByIdAsync(id); if (!n) throw new Error(`node ${id} isn't in the file any more`); return n; };
const slideOf = n => { while (n && n.type !== "SLIDE") n = n.parent; return n; };
const copy = v => { if (v === figma.mixed) throw new Error("mixed values can't be recorded"); return v === undefined ? null : JSON.parse(JSON.stringify(v)); };
const slideNo = s => { const all = figma.getSlideGrid().flat(); return all.findIndex(x => x.id === s.id) + 1; };

async function fit(t) {
  if (t.textAutoResize !== "NONE" && t.textAutoResize !== "TRUNCATE") return { ok: true };
  const c = t.clone(); t.parent.appendChild(c); c.textAutoResize = "HEIGHT";
  const need = c.height; c.remove();
  return { ok: need <= t.height + 1, need: Math.round(need), box: Math.round(t.height) };
}

function swap(t, i, find, repl) {
  if (repl) t.insertCharacters(i + find.length, repl, "BEFORE");   // takes the style of the replaced text
  if (find) t.deleteCharacters(i, i + find.length);
}

// What an op or a card's custom code gets as `k`. Every change goes through it, so it can be undone.
function ctx(map) {
  const undo = [], notes = [];
  const node = id => typeof id === "string" ? (map ? map(id) : get(id)) : id;
  return {
    undo, notes, node, loadFonts, slideOf,
    // Replace `find` with `repl` in a text node, keeping the style. Fails if the words aren't there.
    async text(id, find, repl) {
      const t = await node(id);
      const i = t.characters.indexOf(find);
      if (!find || i < 0) throw new Error(`"${(find || "").slice(0, 40)}" isn't on the slide any more`);
      await loadFonts(t);
      const h = t.height;
      undo.push({ t: "text", id: t.id, at: i, find: repl, repl: find, old: t.characters });
      swap(t, i, find, repl);
      const f = await fit(t);
      if (!f.ok) throw new Error(`the new wording doesn't fit its box (needs ${f.need}px, box is ${f.box}px)`);
      if (t.height > h + 1) notes.push(`"${t.characters.slice(0, 30)}" grew taller`);
      return t;
    },
    // Set properties and remember the old values.
    async set(id, props) {
      const n = await node(id), old = {};
      if (n.type === "TEXT") await loadFonts(n);
      for (const p in props) old[p] = copy(n[p]);
      undo.push({ t: "set", id: n.id, old });
      for (const p in props) n[p] = props[p];
      return n;
    },
    async resize(id, w, h) { const n = await node(id); undo.push({ t: "size", id: n.id, w: n.width, h: n.height }); n.resize(w, h); return n; },
    made(n) { undo.push({ t: "made", id: n.id }); return n; },   // a node you created: undo removes it
  };
}

async function revert(records) {
  const errs = [];
  for (const r of records.slice().reverse()) {
    try {
      const n = await figma.getNodeByIdAsync(r.id);
      if (r.t === "made") { if (n) n.remove(); continue; }
      if (!n) throw new Error(`node ${r.id} is gone`);
      if (r.t === "text") {
        await loadFonts(n);
        let i = n.characters.substr(r.at, r.find.length) === r.find ? r.at : n.characters.indexOf(r.find);
        if (r.find && i >= 0) swap(n, i, r.find, r.repl);
        else if (!r.find && n.characters.length === r.old.length - r.repl.length) swap(n, r.at, "", r.repl);
        else n.characters = r.old;                                   // edited since: put the whole text back
      } else if (r.t === "set") { if (n.type === "TEXT") await loadFonts(n); for (const p in r.old) n[p] = r.old[p]; }
      else if (r.t === "size") n.resize(r.w, r.h);
    } catch (e) { errs.push(String(e.message || e)); }
  }
  return errs;
}

async function runOps(k, ops) {
  for (const op of ops) {
    if (op.op === "replace") await k.text(op.node, op.find, op.repl);
    else if (op.op === "set") await k.set(op.node, op.props);
    else if (op.op === "js") await op.fn(k);
    else throw new Error(`unknown op ${op.op}`);
  }
}

const key = id => "undo:" + id;

// Apply and undo cards in the order given. Returns { "<id>": "ok" | { ok, why?, notes? } }.
async function run(items) {
  const out = {};
  for (const it of items) {
    if (it.do === "undo") {
      const raw = figma.root.getSharedPluginData(NS, key(it.id));
      if (!raw) { out[it.id] = { ok: false, why: "nothing recorded to undo" }; continue; }
      const errs = await revert(JSON.parse(raw));
      figma.root.setSharedPluginData(NS, key(it.id), "");
      out[it.id] = errs.length ? { ok: false, why: errs.join("; ") } : "ok";
      continue;
    }
    if (figma.root.getSharedPluginData(NS, key(it.id))) { out[it.id] = { ok: false, why: "already in the deck" }; continue; }
    const k = ctx();
    try {
      await runOps(k, it.ops);
      figma.root.setSharedPluginData(NS, key(it.id), JSON.stringify(k.undo));
      out[it.id] = k.notes.length ? { ok: true, notes: k.notes } : "ok";
    } catch (e) {
      await revert(k.undo);
      out[it.id] = { ok: false, why: String(e.message || e) };
    }
  }
  return out;
}

// Previews: clone every slide the cards touch, apply the cards to the clones (never the deck),
// and return { clones: { <slide number>: <clone id> } } for get_screenshot. Remove them with drop()
// in a later call: a slide removed in the same call that cloned it comes back.
async function preview(items) {
  const clones = new Map();
  const map = async id => {
    const n = await get(id), s = slideOf(n);
    if (!s) throw new Error(`node ${id} isn't on a slide`);
    if (!clones.has(s.id)) {
      const no = slideNo(s);                           // before cloning: the clone goes in front of it
      const c = s.clone(), a = [s, ...s.findAll()], b = [c, ...c.findAll()];
      clones.set(s.id, { no, c, m: new Map(a.map((x, i) => [x.id, b[i]])) });
    }
    return clones.get(s.id).m.get(id);
  };
  const notes = {}, out = {};
  let why = null;
  try {
    for (const it of items) { const k = ctx(map); await runOps(k, it.ops); if (k.notes.length) notes[it.id] = k.notes; }
  } catch (e) { why = String(e.message || e); }
  for (const { no, c } of clones.values()) out[no] = c.id;
  return why ? { ok: false, why, clones: out } : { ok: true, clones: out, notes };   // drop the clones either way
}

async function drop(ids) {
  for (const id of ids) { const n = await figma.getNodeByIdAsync(id); if (n && n.type === "SLIDE") n.remove(); }
  return { ok: true };
}

return { run, preview, drop, ctx, loadFonts, slideOf };
