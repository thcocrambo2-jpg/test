"""The prompt library's categories and tags, and how an entry is filed.

One **category** per picture — the kind of scene it is — and any number of
**tags** that cut across categories (a step-family scene can also be MILF).
This module is the one place both lists are defined: the admin page writes
them into index.json on every save, and the licence server, the app and
the showcase read their labels from there.

**Keys are permanent.** Entries store keys, so a label can be reworded
freely and a key renamed only by refiling every entry under it.

Filing reads the prompt inside the entry's token, on this machine only,
and stores nothing but the keys. The rules go from the most specific
situation to the most general: a step-family scene shot in a studio is
Step-family, a POV of a couple is POV rather than Homemade couples. They
are rules, not judgement — the admin page shows the result on every card
and either can be changed by hand.
"""

from __future__ import annotations

import re

import store

# (key, label). Display order.
CATEGORIES = (
    ("homemade", "Homemade couples"),
    ("pov", "POV"),
    ("solo", "Solo nudes & selfies"),
    ("tease", "Tease & lingerie"),
    ("stepfamily", "Step-family"),
    ("cheating", "Cheating & risky"),
    ("public", "Public & outdoor"),
    ("serial", "TV serial & film"),
    ("studio", "Studio productions"),
    ("group", "Threesomes & group"),
    ("kink", "Kink & BDSM"),
    ("characters", "Characters"),
)

TAGS = (
    ("milf", "MILF / 35+"),
    ("petite", "Petite"),
    ("saree", "Saree & traditional"),
)

CATEGORY_KEYS = tuple(key for key, _ in CATEGORIES)
TAG_KEYS = tuple(key for key, _ in TAGS)


def index_meta() -> dict:
    """The two lists as index.json carries them."""
    return {
        "categories": [{"key": key, "label": label} for key, label in CATEGORIES],
        "tags": [{"key": key, "label": label} for key, label in TAGS],
    }


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


CHARACTER = _rx(r"\b(sudha|nila|savithriai)\b")
STEP = _rx(r"\b(step-?(mom|mother|dad|father|son|daughter|sister|brother|sis|family|families|relations?)"
           r"|step relations)\b")
SERIAL = _rx(r"\b(television|tv (family )?drama|serial|art-house film|film still"
             r"|in the (visual )?style of an? (indian|malayalam|hindi|tamil|telugu))\b")
STUDIO = _rx(r"\b(adult-studio|adult studio|studio production|production still|adult-film|adult film"
             r"|glossy adult|professional adult|japanese adult-video|jav|staged (realistic )?adult"
             r"|on a set (built|dressed))\b")
GROUP = _rx(r"\b(threesome|foursome|gangbang|group sex|orgy|friend a\b|man b\b|wife swap|swap video"
            r"|lesbian)")
KINK = _rx(r"\b(bdsm|bondage|leather collar|leash|handcuff|blindfold|spank\w*|ball gag)\b"
           r"|tied with [^.]{0,30}rope|rope to the")
CHEATING = _rx(r"\b(cheat\w*|affair|out of (his|her) sight"
               r"|while (her|his) (husband|wife) (sleeps|is asleep|naps|is in the next room)"
               r"|(her|his) husband sleeps|best friend, who stayed|almost caught|nearly caught"
               r"|frozen, staring|could walk in|might walk in|risk of being (seen|caught))\b")
SEX = _rx(r"\b(vaginal sex|anal sex|oral sex|blowjob|cunnilingus|penis|rides him|riding him"
          r"|cowgirl|missionary|(sex|him|her) from behind|from behind (her|standing)|doggy"
          r"|69 position|sex standing|having sex|makes love)\b")
POV = _rx(r"\bpov\b|point-of-view")
PUBLIC = _rx(r"\b(in public|public|naturist|nudist|nude beach|bike ride|metro|bus stop|on a bus|train"
             r"|cinema|theatre|street|lane|market|changing (room|cubicle)|car park|parking"
             r"|beach|jetty|lake|river|waterfall|forest|field|farm|paddy|rooftop|terrace"
             r"|garden|backyard|outdoors?|village|riverbank|hill|park|meadow|sea|shore|boat"
             r"|houseboat|deck|balcony)\b")
# The opening sentence says where the picture is; a "beach towel" later on
# does not make a bedroom picture an outdoor one.
OPENING = _rx(r"^[^.]{0,240}")
NUDE = _rx(r"\b(nude|naked|bare breasts?|both breasts bare|breasts bare|topless)\b")
MASTURBATION = _rx(r"\b(masturbat\w*|fingering herself|touching herself|self-pleasure|vibrator|dildo)\b")
TEASE = _rx(r"\b(lingerie|bra\b|thong|panties|bikini|swimsuit|swimwear|leotard|editorial"
            r"|fully clothed|fully dressed|towel)\b")

AGE = _rx(r"\b(woman|stepmom|stepmother|mom|mother|wife|milf|instructor|stepdaughter|step-sister"
          r"|actress|homemaker|aunty|bhabhi)\b[^.;:]{0,80}?\b(?:of|about|aged) (?:about )?(\d\d)\b")
MILF = _rx(r"\b(milf|stepmom|stepmother|mature woman|in her (late thirties|forties|fifties))\b")
PETITE = _rx(r"\b(petite|very short|short woman|four f(oo|ee)t|4 feet|4'1?\d"
             r"|five f(oo|ee)t( one| tall)?)\b(?![ -](two|three|four|five|six|seven|eight|nine|ten|eleven))")
SAREE = _rx(r"\b(saree|sari|half-saree|lehenga|ghagra|salwar|churidar|mundu|kasavu|choli|mangalsutra)\b")


def classify(prompt: str) -> tuple[str | None, list[str]]:
    """(category key, tag keys) for one positive prompt."""
    text = " ".join(str(prompt or "").split())
    text = re.sub(r"^integrated_visual_description:\s*", "", text, flags=re.IGNORECASE)
    if not text:
        return None, []
    opening = OPENING.match(text).group(0)
    sex = bool(SEX.search(text))

    if CHARACTER.search(text):
        category = "characters"
    elif STEP.search(text):
        category = "stepfamily"
    elif SERIAL.search(opening):
        category = "serial"
    elif STUDIO.search(opening):
        category = "studio"
    elif GROUP.search(text):
        category = "group"
    elif KINK.search(text):
        category = "kink"
    elif CHEATING.search(text):
        category = "cheating"
    elif PUBLIC.search(opening):
        category = "public"
    elif sex and POV.search(text):
        category = "pov"
    elif sex:
        category = "homemade"
    elif MASTURBATION.search(text) or (NUDE.search(text) and not TEASE.search(opening)):
        category = "solo"
    else:
        category = "tease"

    tags = []
    age = AGE.search(text)
    if MILF.search(text) or (age and int(age.group(2)) >= 35):
        tags.append("milf")
    if PETITE.search(text):
        tags.append("petite")
    if SAREE.search(text):
        tags.append("saree")
    return category, tags


def for_token(token: str) -> tuple[str | None, list[str]]:
    try:
        _, data = store.read_token(token)
    except ValueError:
        return None, []
    return classify(str(data.get("values", {}).get("prompt") or ""))
