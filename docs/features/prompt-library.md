# Prompt library

The 🌟 Prompt Library tab: pictures made in this app, each with the settings
token that made it. For someone using it, for the person curating it, and
for someone changing how it works.

A card is a picture, laid out in masonry columns in library order.
**Use** loads its token into the tab that made it, exactly as pasting the
token into that tab's Settings token panel would, and the toast that says
so carries Undo. The braces button copies the token. Above the cards, a
row of category chips (pick one, or All) and a row of tag chips (pick any
number); each chip shows how many pictures it would give with the other
choices kept. While a new choice loads, a thin line runs above the cards
and the old ones dim. There is no prompt text on the card: a prompt only makes that
picture together with every other setting, and the token carries all of
them.

A card for a tab this licence does not grant still shows its picture, with
`🔒 Needs <tab>` instead of the buttons. What a tab you have not bought can
make is exactly what is worth seeing.

## Where it lives

Nowhere but R2. There is no database row behind an entry and no copy on
any pod. Everything is in the private builds bucket, under one prefix:

```
library/prompts/index.json          every entry, in display order, and the
                                    category and tag lists
library/prompts/<id>/thumb.webp     the grid picture (640 px long edge)
library/prompts/<id>/full.webp      the full-size picture (2048 px long edge)
library/prompts/<id>/token.txt      its settings token
```

An entry in `index.json` holds the tab, an optional title, a category, its
tags, two switches and its pictures:

- `enabled` — shown to customers in the Prompt Library tab.
- `public` — shown on the [showcase](#the-showcase).

The two are independent. `media` is a list rather than two fixed fields so
a video and its poster can join it later without touching what is there.

The token is a file of its own, not a field in the index: a token is
several kilobytes, and the licence server reads the index whole. A page
of the library carries **no tokens** — only whether each card is usable
on this licence — so switching category or tag is one small answer. The
token is fetched when Use or Copy is pressed on that card
(`POST /v1/prompt-library/token`, through the pod's
`/api/v1/prompts/<id>/token`), and the server keeps every token it has
read, since a token never changes once its entry exists.

### Categories and tags

Every picture has **one category** — the kind of scene — and any number of
**tags** that cut across categories (a step-family scene can also be
MILF). The lists are defined in
[`scripts/prompt_library/categories.py`](../../scripts/prompt_library/categories.py)
and written into `index.json` on every save, so the server, the app and
the showcase all read their labels from the index.

| Categories | |
| --- | --- |
| `homemade` Homemade couples | `public` Public & outdoor |
| `pov` POV | `serial` TV serial & film |
| `solo` Solo nudes & selfies | `studio` Studio productions |
| `tease` Tease & lingerie | `group` Threesomes & group |
| `stepfamily` Step-family | `kink` Kink & BDSM |
| `cheating` Cheating & risky | `characters` Characters |

Tags: `milf` MILF / 35+, `petite` Petite, `saree` Saree & traditional.

**Keys are permanent** — entries store them. A label can be reworded in
`categories.py` freely; the next admin save carries it everywhere.

A picture is filed from the prompt inside its token, on your machine,
by rules checked from the most specific situation to the most general (a
named character, then step-family, then serial or studio style, then
group, kink, cheating, outdoor, POV, other sex, solo nude, and tease for
the rest). Only the keys are stored. They are rules, not judgement — the
admin page shows the result on every card and either can be changed.

Three things read that one file:

| Reader | Sees | Through |
| --- | --- | --- |
| The Prompt Library tab | `enabled` entries, tokens only for granted tabs | `POST /v1/prompt-library` on the licence server, which signs each picture link |
| The admin page | everything | R2 directly, with the write key |
| The showcase | `enabled` **and** `public` entries, **never a token** | R2 directly, with the read key |

The pod's side is [`ember/licensing/prompts.py`](../../ember/licensing/prompts.py),
the server's is `license-validator/src/promptLibrary.js`. The pictures go
from R2 straight to the browser through signed links; the pod never
downloads one.

### Picture links

A link lasts six hours and is signed as if at the top of the current hour.
So one picture keeps the same link for the whole hour, which lets the
browser cache it, and every link handed out has at least five hours left.
A link opens one picture, cannot list anything, and stops working when it
expires.

### Metadata

Every picture is decoded and re-encoded from its pixels into a fresh image
on upload, so no EXIF, XMP, ICC profile or PNG text chunk survives — and
ComfyUI writes the whole prompt and workflow into those text chunks. The
upload checks the WebP it wrote and refuses one that still carries any.
Object names are random ids, never the source file's name, which may hold a
prompt or a seed. The only things in the bucket beside the pixels are a
Content-Type and a Cache-Control.

## Curating it: the admin page

```bash
# on your own machine, from the repo root
python scripts/prompt_library/admin.py        # http://127.0.0.1:7890
```

It reads the R2 **write** key from `license-validator/.env`
(`R2_WRITE_ACCESS_KEY_ID` / `R2_WRITE_SECRET_ACCESS_KEY`), so it answers
only to this machine: it binds 127.0.0.1 and refuses any request whose
`Host` is not localhost. A tunnel pointed at it by mistake gets a 403.

**Adding pictures.** A pair is a picture and a `.txt` holding its token,
with the same name — `Krea_00012_.png` and `Krea_00012_.txt`, which is
exactly what Gallery's zip download and its auto-download write. Drop any
of these onto the page:

- a Gallery zip;
- a folder (or use *Choose a folder…*), paired file by file;
- pictures and their `.txt` files together.

There is also a single picture + pasted token form. Each pair is checked
before anything is uploaded: the token must decode, and its tab is read
from it. A token already in the library is skipped. A picture with no
token beside it, or a token with no picture, is listed rather than guessed
at.

**New entries start switched off and not public.** Nothing reaches a
customer until you tick it. Each is filed under a category and tags from
its prompt as it arrives.

**Everything else** is on the cards: the *Customers* and *Showcase*
switches, a title, the category, the three tags, Copy token, To front,
Delete. Select several to switch them, set their category, add or remove
a tag, re-file them from their prompts, or delete them in one go. The
filters at the top narrow by tab, state, category (including
*Uncategorised*) and tag. **Auto-file uncategorised** files every entry
that has no category yet and leaves hand-set ones alone. Drag cards to set the order customers see (with
no filter on). Delete takes the entry out of the index first and then
removes its pictures, so a failure halfway leaves an unreachable object,
never an entry with a missing picture.

Every change is saved to R2 at once, as a conditional write against the
index it read. If the index changed underneath — a second admin page open
somewhere — the save is refused and the page reloads, rather than one copy
silently erasing the other's work.

A change reaches customers within about six minutes: the licence server
reuses one read of the index for a minute, and a pod caches a page for
five. The tab's Refresh button skips the pod's half.

## The showcase

A page of the library's public pictures for showing a client, run from
your laptop only while you are showing it.

```bash
python scripts/prompt_library/showcase.py               # http://127.0.0.1:7891
python scripts/prompt_library/showcase.py --code 4821   # choose the code
```

It prints an access code (a fresh six-digit one unless you pass `--code`)
and asks for it before showing anything. In person, open the local address.
For someone remote, put a free quick tunnel in front of it and send them
the link and the code:

```bash
cloudflared tunnel --url http://127.0.0.1:7891
```

Stop both when you are done; the tunnel's link dies with it. Ten wrong
codes lock the page until it is restarted.

What a client can get from it:

- only entries that are on **and** public;
- a picture, the tab's name, the title, the category and tags (with the
  same category and tag chips as the app) — the entries are trimmed to
  those fields before they leave the process, so **no token or prompt is
  ever sent**, and the pictures carry no metadata;
- picture links that load straight from R2 (not through your upload
  bandwidth) and expire like the app's.

It holds the R2 **read** key only, and sends `noindex`, `no-referrer` and
`DENY` framing headers.

## Related

- [presets](presets.md) — the settings without the prompt, a dropdown on
  each tab.
- [Licensing and features](../architecture/licensing-and-features.md) —
  `community_prompts` is the feature key, and it needs no weights.
