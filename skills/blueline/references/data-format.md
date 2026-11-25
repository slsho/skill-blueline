# data.json

The review page (`app/index.html`) renders whatever is in `data.json`. A full worked
example is `docs/data.json` in the repo (7 comments from 4 reviewers on a 19-slide deck).

```jsonc
{
  "demo": false,                                      // true only for a static demo: no server, nothing is sent
  "deck": { "title": "Ecosystem mapping playbook", "url": "https://www.figma.com/slides/<key>", "round": "Review round 1", "slides": 19 }, // slides: how many slides the deck has, for "Slide 4 of 19"
  "people": { "priya": "Priya Natarajan" },            // short id -> full name
  "highlights": { "after-03": [[9.4, 70.0, 83.7, 6.0]] }, // after-image -> boxes [left, top, width, height] in %
  "me": { "name": "Sam Rivera", "img": "https://..." },  // optional: the owner's Figma name and photo, shown on their replies
  "stops": { "5": { "before": "before-05", "after": "after-s05",       // optional: a slide with several comments, every change in one picture
                    "pins": { "4": [[6.0, 30.6, 9.6, 4.4]], "5": [[3.8, 90.6, 32.1, 4.4]] } } },  // card id -> its boxes on that picture
  "cards": [ /* one per comment thread, in slide order */ ]
}
```

## A card

| field | used for |
|---|---|
| `id` | number, unique |
| `commentId` | the Figma thread id, so the reply lands in the right thread |
| `slide` | slide number this card sits on (its stop on the page) |
| `kind` | `change`, `reply` or `pick` |
| `where` | label like `"Slide 2"` or `"Slides 5 and 10"` |
| `who` | key into `people` |
| `comment` | the reviewer's words, exactly |
| `replyFrom` | optional `[who, text]`: a second reviewer replying in the thread |
| `change` | one sentence: what Claude did (change and pick cards) |
| `redline` | optional list of HTML lines using `<del>`, `<ins>` and `<span class="ctx">`. The page keeps only those (and `<b>`, `<i>`, `<em>`, `<strong>`, `<br>`); any other tag shows as plain text |
| `edit` | optional list `{ ctx?, find?, after }`, one per redline line: the new wording the owner can tap to change, and the text it replaces |
| `why` | Claude's reasoning, shown behind "Why" |
| `reply` | the draft reply to the reviewer. A `reply` card starts with it written out; on `change` and `pick` cards it stays hidden until the owner chooses to reply, then Ask Claude fills it in |
| `shots` | change cards: `[[before-image, after-image, caption], ...]`, image names without `.png` |
| `more` | optional note under the thumbnails, like `"+ slides 3, 6 and 8"` |
| `image` | reply cards: the slide image to show |
| `alsoOn` | optional slide numbers this change also touches; those stops link back here |
| `short` | optional short name for the "Also on this slide" link |
| `range` | pick cards: `[[slideNumber, image], ...]` shown as a grid |
| `options` | pick cards: `{ key, name, count, desc, strip: { "<slide>": "" \| "cut" \| "fold" \| "plus" }, images?: { "<slide>": after-image }, newAfter?: [<slide>] }`. Give every slide an option adds content to (`plus`) its after-image, so the owner sees the exact words before approving. `newAfter` lists slides that get a brand-new slide inserted after them rather than a change to themselves; the new slide's picture is `images["<slide>"]`. The page labels those slides "New slide after slide N" |
| `pick` | pick cards: the suggested option key |
| `ops` | change cards (pick cards: on each option, `[]` if it changes nothing): what to change, as data. `figjs.py` turns them into the preview, the apply and the undo. `{"op": "replace", "node": "3:16", "line": 0}` swaps edit line 0's `find` for the owner's wording; also `{"op": "set", "node", "props"}` and `{"op": "js", "file": "apply/4.js"}`. A card without `ops` still works, but you apply it by hand, slowly |
| `apply` | instructions for yourself: exactly what to change in Figma. The page doesn't show it. |

Images live in `img/` next to `data.json`. Every image is 16:9 (1280x720 is the norm); the page
shows them in a 16:9 frame, so any other shape is cropped and its outlines drift.

## While the round runs

Leave `data.json` as the owner first saw it, except when you redo a card they sent back
(Not quite). Then rewrite that card in place (same `id`), give its new after-image a new file
name, add the new outline boxes to `highlights`, and only then mark the card `open` with
`progress.py`: the page reloads `data.json` when it sees that.

The server keeps the owner's decisions in `acts.json` and your progress in `progress.json`, both
in the review folder. You never edit `acts.json`; write `progress.json` only through `progress.py`.
