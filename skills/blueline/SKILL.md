---
name: blueline
description: Work through the reviewer comments on a Figma Slides deck. Claude drafts a change or a reply for every comment and shows them on a review page (one slide per stop, the change outlined). Each time the deck owner approves a card, Claude applies that change in Figma and posts its reply into the comment thread, while the owner keeps reviewing. Use when someone asks to review, triage, address, answer or apply feedback or comments on a Figma Slides deck.
argument-hint: "<figma-slides-url>"
---

# Blueline: reviewer comments in, approved changes out

The deck owner should only have to read and decide. You do the rest: read every
comment, draft the fix, show it, then apply exactly what they approved, one card at a
time, as they approve it.

Bundled files (paths are inside this skill):
- `${CLAUDE_SKILL_DIR}/app/index.html`: the review page. It reads `data.json` next to it.
- `${CLAUDE_SKILL_DIR}/scripts/serve.py`: serves a review folder and records each decision the owner makes.
- `${CLAUDE_SKILL_DIR}/scripts/next.py`: waits until the owner gives you something to do, then prints it,
  with the Figma code for it ready to run. It also posts approved replies on its own.
- `${CLAUDE_SKILL_DIR}/scripts/figjs.py` + `blueline.js`: turn a card's `ops` into `use_figma` code for
  previews, applying and undoing. The runtime is stored in the deck once, so each call is a few lines.
- `${CLAUDE_SKILL_DIR}/scripts/progress.py`: tells the page how a card is going, for anything next.py doesn't.
- `${CLAUDE_SKILL_DIR}/scripts/fetch_comments.py`, `post_replies.py`: Figma comments over the REST API.
- `${CLAUDE_SKILL_DIR}/scripts/diffboxes.py`: finds the changed areas between two slide images (needs Pillow).
- `${CLAUDE_SKILL_DIR}/references/data-format.md`: the `data.json` card format. Read it before step 4.
- `${CLAUDE_SKILL_DIR}/references/figma-recipes.md`: tested Plugin API snippets for the edits. Read it before step 3.

## Before you start

1. **Figma connector.** You need the Figma MCP tools (`use_figma`, `get_screenshot`). If the
   `figma-use-slides` skill is available, load it before the first `use_figma` call and pass it
   in `skillNames`. Follow its rules: `appendChild` before setting `x`/`y`, no `get_metadata` on
   Slides files, never delete a slide.
2. **Figma token** for comments. The scripts read `FIGMA_TOKEN` from the environment or from a `.env`
   in the current folder, so run them from the folder that holds the user's `.env`. If it says there's no token, point the user at
   `.env.example` and stop until they've added theirs. Never print, echo or commit the token.
3. **Which deck gets edited.** Changes are written into the deck at the URL you were given, and the
   previews use temporary copies of slides inside it. Say so, and suggest a duplicate
   (Figma: file name > Duplicate) for a first run. Comments don't copy with a duplicate, so the
   comments must be on whichever file you use.

## 1. Collect

- `fetch_comments.py <url> > <review-dir>/threads.json`. The review folder is
  `./blueline-review/<file-key>/` in the user's current folder.
- Read the deck with a read-only `use_figma` script: slides in order (walk `SLIDE_GRID` >
  `SLIDE_ROW` > `SLIDE`), each slide's text, and which slide each comment's `node_id` sits on
  (walk up `parent` until you reach a `SLIDE`).
- Look for house rules: a design-system or writing-rules file the user points you at, or
  obvious patterns in the deck (palette, label style, one accent per slide). Respect them.

## 2. Decide what to do with each comment

Pick a `kind` for every thread:
- `change`: the ask is clear. Draft the edit.
- `reply`: vague ("doesn't land for me"), a question about a source, praise with no ask, or a
  request that breaks the deck's design system. Don't guess. Draft a short reply that asks,
  answers, thanks or explains.
- `pick`: two reviewers want opposite things. Give 2 or 3 options, mark the one you suggest.

Then:
- A change that spans several slides is one card, placed on its first slide, with `alsoOn` listing
  the others so they point back to it instead of asking twice.
- A question that also earns a slide fix (a missing number, a softer claim) is a `change` with a
  `reply` drafted. On a `change` or `pick` card the page keeps that reply out of sight until the owner
  chooses to reply (Reply to Tom, then Ask Claude fills in yours), so don't write "Answered Tom" in
  `change`; say in `why` that a reply is ready. Only `reply` cards start with the reply written out.
- Two cards that edit the same text: say in each card's `why` how they combine, and make each
  card's `apply` work whether or not the other one is approved.
- Write for the deck owner: plain words, short. Say what changed in one sentence; put your reasoning
  in `why`. No em dashes. Quote the reviewer exactly.
- Any number in the card ("36 circles on 10 slides") comes from what you counted in the deck, not
  from a guess. Recount when you apply and fix the card if it differs.

## 3. Make the previews

Speed matters: the owner is watching. Approving a text change should land in the deck in seconds,
or they'll stop trusting the tool and edit by hand. So every change is written once, as data, and the
same `ops` drive the preview, the apply and the undo.

- **Install the runtime first** (once per round, and again if any call returns `needLib`):
  `python3 ${CLAUDE_SKILL_DIR}/scripts/figjs.py <review-dir> install`, and run what it prints with `use_figma`.
- **Write each change card's `ops`** in `data.json` (pick cards: on each option; `"ops": []` for an
  option that changes nothing). Text edits are `{"op": "replace", "node": "<text node id>", "line": <j>}`,
  one per place the words appear: line `j` of the card's `edit` supplies the old words (`find`) and the
  new ones (the owner's wording, if they changed it). Property changes are `{"op": "set", ...}`.
  Anything else (new shapes, layout) is a `js` op file; see the recipes file. Format in `figjs.py`.
- **Preview with the same ops:** `figjs.py <review-dir> preview <id>[,<id>...]` prints code that clones
  every slide those cards touch, applies them to the clones and returns `{clones: {slideNo: cloneId}}`.
  Screenshot the clones, then remove them in a later call with `figjs.py <review-dir> drop <ids>`
  (a clone removed in the same call that made it comes back). If it returns `ok: false`, drop its
  clones too and fix the ops: the same thing would fail when the owner approves.

For each slide involved:
- **Before:** `get_screenshot` of the slide (`maxDimension` 1280), saved with `curl -sL -o` to
  `<review-dir>/img/before-NN.png` (the URL is short-lived, fetch it straight away).
- **After:** the clone from `figjs.py preview`, screenshotted to `img/after-<card>-sNN.png`, then dropped.
  Batch: preview several cards in one call (each card's own slides), and drop the last batch's clones
  in the same call as the next preview. Only remove clones you made in this step.
- **Outlines:** `python3 ${CLAUDE_SKILL_DIR}/scripts/diffboxes.py <review-dir>/img before-NN after-...`
  gives the changed boxes. Merge boxes that overlap; drop specks.
- `reply` cards need only the before image.
- Every image must be 16:9: the page shows each one in a 16:9 frame and crops anything else, which
  also throws the outlines off. For a close-up, crop a 16:9 area from both the before and the after
  slide (same box), scale it to 1280x720, and outline the whole element that changed.
- `pick` cards: for every slide an option adds content to, make an after-image and list it in that
  option's `images`, so the owner reads the exact new words before approving. Put those words in
  `apply` too, and say in the option's `desc` anything it changes that has no picture (a contents
  line, page numbers).
- **A slide with several comments:** also make one picture with every proposed change on that slide
  applied together, including changes decided on another slide that touch it (`alsoOn`):
  `figjs.py preview 4,5` puts both cards on one clone per slide. Screenshot to `img/after-sNN.png`, remove the clone. List it under `stops` in
  `data.json` with each card's own boxes (`pins`), so the owner sees every comment's pin at once and
  approves them one at a time. Boxes from each card's own after-image usually still fit; check them
  against `diffboxes.py before-NN after-sNN`. If you redo a card on that slide, redo this picture too.
- The runtime checks every edited text still fits a fixed-size box and refuses the card if not, so a
  preview that came back `ok` will fit when applied in your wording.

## 4. Build and open the review page

- Copy `${CLAUDE_SKILL_DIR}/app/index.html` into the review folder and write `data.json`
  (format in `references/data-format.md`). Include the deck's slide count (`deck.slides`), each thread's Figma `commentId`, the
  `ops` per change card (step 3), an `apply` note per card (what exactly to change, where, in which style), and `me` (the owner's Figma
  name and photo, if you have them) so their replies show under their name.
- Start the server in the background:
  `python3 ${CLAUDE_SKILL_DIR}/scripts/serve.py <review-dir> --port 4400`
  (if the port is taken, pick another and use it in the link below).
- Open `http://localhost:4400/` (the browser pane if you have one, otherwise give the link).
- Start waiting for the owner right away (step 5), then tell them, in two or three lines: scroll
  through the slides, tap any wording to change it, press **Approve** on a card to put it into the
  deck now (or **Post** for a reply), **Not quite** to send it back to you with a note. They can
  keep going while you work.

## 5. Work through the owner's decisions as they come

The page records every decision the moment it's made. `next.py` picks them up, marks them started,
posts approved replies itself, and hands you the rest with the Figma code already written. Per batch
of approvals that's one `use_figma` call and one `next.py` call. Don't add steps to that path: no
screenshot after a text change (the runtime checked the text and the fit), no separate progress calls.

The loop:
1. Run `python3 ${CLAUDE_SKILL_DIR}/scripts/next.py <review-dir> --claim --post` in the background, from
   the folder with the `.env`. It ends, and you're notified, as soon as there's work for you. Its
   output is `{"work": [...], "round": {...}, "js": "..."}`, oldest first. Approved reply-only cards,
   picks that change nothing and late replies never show up: it posts or closes those on its own.
   Undo on a posted reply (a reply-only card or a late reply) doesn't either: it takes the reply
   back out of the thread itself.
2. If there's `js`: run it with `use_figma` exactly as printed (it covers every `apply` and `undo`
   item that has ops, in order). It returns `{"<id>": "ok" | {"ok": false, "why": ...}}`; a card that
   failed was rolled back whole. If it returns `needLib`, run `figjs.py <review-dir> install`, then the `js` again.
3. Straight away, start the next wait and hand it the result:
   `next.py <review-dir> --claim --post --done '<the result JSON>'` (background). That marks applied cards
   in the deck (posting their reply first), undone cards back to open (taking their reply out of the
   thread first), and failures with their reason.
4. Items without `js`:
   - **`apply` with `"manual": true`** (the card has no `ops`): write the change yourself with
     `use_figma`, compare a screenshot with the approved preview, post its `reply` if any with
     `post_replies.py <url> --thread <commentId> --text -`, and mark it `in` or `failed --why` with
     `progress.py <review-dir> card <id> <state> --n <the item's n>`. With `"undo_first": true` an
     earlier version of the card is still in the deck: undo it first (`figjs.py <review-dir> undo <id>`),
     and take its `prior_reply` out of the thread if the new reply differs.
   - **`redo`** (Not quite, already marked `revising`): read the owner's `note`, redraft the card in
     `data.json` (wording, `edit`, `ops`, `redline`, `why`, `apply`), preview it again under a **new
     image name** (`after-<card>-sNN-r2.png`; the browser keeps old images under old names), update
     `highlights`, then `progress.py <review-dir> card <id> open --n <the item's n>`. The page reloads the card,
     and any wording the owner had typed over the old draft gives way to the new one.
   - **`draft`** (the owner pressed Ask Claude on an empty reply box): write a short reply in the deck's
     voice for that thread and send it with `progress.py <review-dir> draft <n> --text -`. It lands
     in the box for the owner to edit; nothing is posted.
   - **`unpost`** only shows up if `next.py` ran without `--post`: take the reply back out with
     `post_replies.py <url> --thread <commentId> --text - --delete`, then `progress.py <review-dir> card <id> open`
     (or `unlater <n> done` when the item has `of`).
   - A non-text change that `js` applied: if it moved or added anything, screenshot that slide once
     and compare it with the approved preview; if it's off, tell the owner.
5. Keep going until `round.undecided` and `round.with_claude` are both empty, then report (step 6).
   Leave the server and one last `next.py` running in case the owner adds a late reply, until they say
   they're done.

Undo records live in the deck itself (shared plugin data, per card), so any applied card can be taken
back with `figjs.py <review-dir> undo <id>` until the owner is done, even from a new session.

## 6. Report

One short message: what went into the deck, what failed and why, which replies were posted, which
cards were skipped. If any card was sent back and you redid it, say what changed. Stop the server
when the owner is done.

## Rules

- Change only what was approved, in the approved wording. Nothing else on the slide moves.
- Never delete slides or layers you didn't create. Never resolve or delete reviewers' comments.
- Post a reply only after the owner approved it (Approve, Post, or the send button on a late reply).
- Let `next.py --claim` mark items started; anything you take on outside it, mark with `progress.py` first.
  Always pass `--n` with the item's `n`: if the owner decided again meanwhile (from another window), your
  state lands on the decision you handled and the newer one stays in the queue.
- If a script stops because `acts.json` or `progress.json` can't be read, don't delete or empty it: tell
  the owner which file it is. Reading it as empty would apply and post finished cards a second time.
- Keep the token out of logs, files and commits.
