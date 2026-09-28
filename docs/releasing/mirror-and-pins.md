# The mirror and the pins

For whoever keeps the weights available. Every weight this app downloads
has two possible sources and one correct answer:

1. **your Hugging Face mirror** — tried first, at the pinned revision;
2. **the original upstream repo** — fallback, at the *same* pinned
   revision.

Mirroring protects against deletion; pinning protects against change. The
second is the one that bites sooner, and it is the reason both sources
must resolve to the same revision: a fallback that silently returns
*different* weights than the mirror is worse than no fallback at all,
because nothing in the logs says the image you just generated came from
other bytes.

## The two data files

Both live in [`scripts/`](../../scripts/), deliberately outside the
package so Nuitka never sweeps the operator tooling into the shipped
binary. `build.sh` copies them next to the code for the frozen build, and
[`ember/weights/mirror.py`](../../ember/weights/mirror.py) looks in both
places rather than making the two layouts disagree.

| File | Holds |
| --- | --- |
| [`scripts/mirror_manifest.json`](../../scripts/mirror_manifest.json) | what lives in the mirror, and where |
| [`scripts/PINS.json`](../../scripts/PINS.json) | the revision every source must resolve to |

`mirror_manifest.json` has four parts:

- **`repos`** — the mirror repositories and what each is for: `loras`
  (CivitAI LoRAs, highest churn), `encoders` (the merged abliterated
  Qwen3-VL encoder), `assets` (community HF weights) and `nodes` (pinned
  custom-node tarballs). `repo_id()` turns a key into
  `<your-user>/<name>`.
- **`items`** — the **pipeline** assets: the merged text encoder, the
  VAEs, the Identity Edit LoRA, Z-Image's `4xLSDIR.pth` upscale model.
  Each names a `local` path under `MODELS_DIR`, a `path_in_repo`, and the
  `source` it was fetched from.
- **`node_packs`** and **`pin_upstream`** — the custom-node packs and the
  upstream repos that get a revision recorded in `PINS.json`. The packs
  are RES4LYF, RBG Smart Seed Variance, the post-processing nodes,
  comfyui-krea2edit and ComfyUI_UltimateSDUpscale.
- **`mirror_public`** — whether the mirror repos are public. When they
  are, `mirror.token()` returns `None` on purpose: passing a customer's
  token to a public repo would work, but a stale or malformed `HF_TOKEN`
  turns a download that needs no credential into a 401, and the mirror
  would appear to be missing files it plainly has.

**The catalogue's models and style LoRAs are not in the manifest.** They
live in the licence-server database, and each record carries its own
`mirror` (`{repo, path}`, or null). Adding a LoRA is a database edit
followed by a mirror run, never an edit to the manifest.

Pins come in two shapes. An HF repo records `{"kind": "hf", "sha": …}`
and is fed to `hf_hub_download(revision=…)`. A git repo records
`{"sha": …, "url": …}` plus, for a node pack, the `tarball` name in the
`nodes` mirror repo, and drives the pinned clone in
`ember/comfy/setup.py`. `mirror.revision()`, `mirror.node_pin()` and
`mirror.comfyui_sha()` are the only readers.

### The ComfyUI pin

ComfyUI itself is pinned the same way, under `ComfyUI` in `PINS.json`,
at **v0.37.4** (`8ff6dc38`). Every tab runs on it, so it moves only when
a tab needs a newer release, and the floor is set by the tabs whose nodes
are core ComfyUI: MiniMax needs v0.34.0 (`MINIMAX_COMFYUI_MIN`).

A checkout that already exists (a pod volume, a Windows install, the
Docker image's baked tree) is moved to a new pin on its next start by
`repin_checkout()` in `ember/comfy/setup.py`, and ComfyUI's
requirements are reinstalled straight after, under the torch
constraints file, so a release that bumps its frontend or kernel
packages cannot move torch.

Before moving it, check against a checkout at the new revision:

- every snapshot in `scripts/golden/` passes `POST /prompt`;
- every node pack in `node_packs` imports, and the classes `main.py`
  verifies are registered;
- a catalogue LoRA of each kind still maps all its keys (LoRA loaders
  change between releases; the Z-Image LoKr is the unusual one);
- `repin_checkout()` moves a checkout at the old pin.

`ember/weights/mirror.py` answers questions about these two files and
nothing else. It downloads nothing — `ember/weights/downloads.py` and
`ember/comfy/setup.py` do that — so the policy stays in one place and the
transport stays in theirs. If either file is missing the app still runs
and says so in the log: without a manifest every download goes straight
upstream, and without pins nothing is pinned to a revision, so upstream
can change weights under you. `mirror.describe()` is the one line in the
startup log that says which of those you are in.

## Mirroring to Hugging Face

**POD** (bash). Needs `HF_WRITE_TOKEN` and an operator key issued with
every feature granted, because what gets downloaded comes from the
licence. The whole thing is idempotent and resumable: the mirror repo,
not the local disk, decides the work.

```bash
# 1. boot a pod with everything on and let it finish downloading (~220 GB)
EMBER_LICENSE_KEY=<operator key> python3 app.py

# 2. capture pod state — the revisions everything resolved to
python3 scripts/mirror_to_hf.py --pins-only

# 3. what has no mirror
python3 scripts/mirror_to_hf.py --audit

# 4. read the checklist
python3 scripts/mirror_to_hf.py --dry-run

# 5. top up and upload (~18 GB — only what is at risk of disappearing)
EMBER_ADMIN_TOKEN=<token> HF_WRITE_TOKEN=<token> python3 scripts/mirror_to_hf.py
```

Flags: `--dry-run`, `--audit`, `--pins-only`, `--public`,
`--skip-downloads`, `--include-disabled`, `--only KEY` (repeatable),
`--staging PATH`, `--catalog FILE`.

`--audit` and `--dry-run` need no `HF_WRITE_TOKEN` and no
`EMBER_ADMIN_TOKEN`, and neither writes anything to the mirror or to the
database.

An HF repo already in `PINS.json` keeps its pin even when upstream has
moved on, because pods downloaded at that revision; the log names the
newer one, and moving a pin is an edit to `PINS.json`. Only a repo with
no pin yet gets upstream's current revision. A pack's pin follows the
checkout it is packed from, and a pin a run cannot see, such as a pack
whose feature was off or one set by hand, is carried forward.

### Topping up a few files

A new manifest item or node pack doesn't need the ~220 GB pod.
`--only assets --only nodes` leaves out the `loras` repo, so no catalogue
LoRA is uploaded and no database record is written. With a fresh
`EMBER_BASE_DIR`, the run packs only what you clone into its
`ComfyUI/custom_nodes/`: each pack at its `PINS.json` sha, submodules
included. It needs only `huggingface_hub` and `requests`. Commit the `tarball` name the run adds
to `PINS.json`: pods use a pack's tarball only once its pin names it.

Steps 3–5 read the catalogue the way the pod does — `POST /v1/catalog`
with `EMBER_LICENSE_KEY` and `EMBER_NODE_TAG` — or, with
`--catalog FILE`, from a file in the same shape
([`license-validator/data/assets.json`](../../license-validator/data/assets.json)
is one).

## Why only ~18 GB of ~220 GB is mirrored

Only what is actually at risk of disappearing: the CivitAI LoRAs, the
community HF repos and the GitHub node packs. The Comfy-Org repos stay
upstream — they are org-backed and built to serve that traffic. What they
need instead is a pinned revision.

The same rule covers the catalogue: a record whose source is an HF repo
that `pin_upstream` pins, and that no manifest item mirrors from, needs
no mirror and is not reported as missing one. `mirror.location()`
returning `None` means exactly that — "not mirrored, go upstream" — and
it is the correct answer for the bulk of the Comfy-Org weights.

Z-Image shows both sides of the rule. Its diffusion model, text encoder
and VAE come from `Comfy-Org/z_image_turbo`, pinned and upstream-only.
Its upscale model comes from `Hearmeman/comfyui-template-assets`, a
community repo, so it is pinned *and* has a manifest item in the
`assets` repo. ComfyUI_UltimateSDUpscale is pinned with no `tarball`
until a mirror run packs one, so pods clone it at the pin; the pack's
git submodule is checked out with it, and a mirror run's tarball carries
the submodule's files too, since it is packed from the working tree.

Catalogue **models** are only audited. The script uploads catalogue
LoRAs, not catalogue models.

## Adding a LoRA

1. Add its record to the licence-server database.
2. Run `mirror_to_hf.py`. A LoRA whose `mirror` is null is fetched from
   its `source` (from local disk first, never from the mirror), uploaded
   to the `loras` repo at `loras/<file>`, and the location is written
   back to its record with `POST /v1/admin/loras`, authorised by
   `EMBER_ADMIN_TOKEN`.
3. Without that token — or without `EMBER_NODE_TAG` — the script prints
   the `npm run assets -- --mirror …` command that records it by hand
   instead.

A pipeline asset stays in the manifest after the code stops naming it.
That is the point of a mirror.

## Turning it off

`EMBER_NO_MIRROR` disables mirror lookups entirely, and `EMBER_MIRROR_USER`
chooses whose mirror to read. Both are read in
[`ember/settings.py`](../../ember/settings.py) and documented in
[configuration](../configuration.md).
