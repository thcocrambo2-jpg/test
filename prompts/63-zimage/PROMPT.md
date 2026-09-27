# Add a Z-Image Turbo tab (with an optional 1.5x upscale)

You are working in the Ember repo (the `ember/` package, the React app in
`webui/` and the licence server in `license-validator/`). Implement one new
image-generation tab: **Z-Image Turbo**, with a tick box that adds a 1.5x
upscale pass. The box is off by default.

Work on branch **`63-zimage`**, which is based on `62-min`. This prompt and the
two reference workflows are committed on it under `prompts/63-zimage/`.
(`prompts/` is in `.gitignore`, so they were force-added. Leave them there,
and don't `git add` anything else under `prompts/`.)

Read this whole file before you start. If something here contradicts what
you find in the code, trust the code, say so in your report, and choose the
option that changes existing behaviour least.

---

## 1. What the user wants

- **One tab** that replaces two workflows from the Hearmeman24
  `comfyui-qwen-template` repo:
  - `prompts/63-zimage/reference/Z_Image_Turbo.json`: text to image.
  - `prompts/63-zimage/reference/Z_Image_Turbo_Upscale.json`: the same
    graph plus an UltimateSDUpscale 1.5x pass.

  On the tab, a **"Upscale 1.5x"** checkbox (default **off**) switches
  between them. The user has already tried both in plain ComfyUI and wants
  the app to match them.
- **The same layout and components as the other generation tabs** (🎨 Krea2,
  🔶 Krea2 V2): its own tab in the navigation, with sections on the left
  (Prompt, Output, Sampler, Seed & batch, Presets), the result image on the
  right, and the LoRA stack at the bottom. It needs a model dropdown, a LoRA
  stack and settings like the Krea tabs have. Controls with no Krea
  equivalent (the upscale tick, the resolution multiplier) go into a
  sensible section, not a one-off layout.
- **In the database, but on no plan.** The feature goes into the licence
  server's catalogue (features, models, feature_assets) in the seed files
  *and* the production database. It must **not** be added to any plan in
  `license-validator/src/plans.js` or in Atlas, not even `admin`. The user
  will grant it to their own key with `features_extra` to test it, then put
  it on a plan later themselves.
- **Nothing that works today may break.** Every existing golden stays
  byte-identical, and every existing check still passes.
- **Update the docs properly** (see §7).
- **Deploy nothing.** No Vercel deploy of the licence server, no build or
  release, no pod, no R2 or HF mirror uploads, and no push. The user
  deploys and pushes by hand. (Pushes from this machine fail with a 403
  anyway.) The one outside write this task allows is the production DB
  insert in §6, and only after a backup.

## 2. Decisions already made (don't re-ask)

| Topic | Decision |
| --- | --- |
| Feature key | `zimage_t2i` (the `<model>_<task>` convention). The key is permanent once it ships. |
| Tab label / icon | `⚡ Z-Image`, icon `⚡`, blurb along the lines of "Fast, realistic text-to-image." |
| Schema | `tab_id="zimage"`, `route="/generate/z-image"`, `category="generate"`, `submit_label="Generate"`, `lane=runner.COMFY_LANE`, `result_keys=IMAGE_KEYS`. Put it in `SCHEMAS` right after Krea2 V2. |
| Layout base | Copy 🎨 Krea2's schema shape (`ember/web/schema/tabs/krea2.py`, `KREA2_SCHEMA`). Z-Image uses a plain KSampler, which maps one-to-one onto its controls. |
| Upscale | **The same nodes as the reference workflow**: the `ComfyUI_UltimateSDUpscale` pack (ssitu) plus the `4xLSDIR.pth` upscale model. Don't approximate it with core nodes. |
| LoRAs | Yes: the standard model-only stack (`_blank_lora_tail`), backed by the catalogue list for `zimage_t2i`, which is **empty for now**. Krea LoRAs don't work on Z-Image, so don't share them. The user will add Z-Image LoRAs later with DB edits. |
| Presets | Yes, like the MiniMax tabs: save-preset only (`G_SAVE_PRESET` + `_preset_save_fields()`), under preset tab `zimage_t2i`. **No prompt-library publish.** The Prompt Library only knows the Krea tabs, and extending it is out of scope. |
| Plans | None, as described in §1. |

## 3. The reference graph (what to transcribe)

Read both JSONs yourself; this section summarises them. Node ids are the
ones in the files.

**Base graph (both files):**

- `UNETLoader` (96): `z_image_turbo_bf16.safetensors`, weight dtype `default`.
- `CLIPLoader` (99): `qwen_3_4b.safetensors`, type **`qwen_image`**, device
  `default`. Before relying on it, check with `/object_info` on the
  **pinned** ComfyUI that `qwen_image` is a valid `CLIPLoader` type and
  loads a Qwen3-4B encoder correctly. If it doesn't, use `lumina2`, which is
  what ComfyUI's own Z-Image template uses, and say so in the report.
- `VAELoader` (39): `ae.safetensors`.
- LoRA chain: four `LoraLoaderModelOnly` nodes (101 → 103 → 170 → 201). All
  are **bypassed** placeholders, so they map onto the tab's LoRA stack.
  `ModelPassThrough` (179, KJNodes) is only a wiring helper; leave it out.
- The prompts come from `Text Prompt (JPS)` nodes, then `CLIPTextEncode`
  positive (6) and negative (7). JPS is just a text box; leave it out.
- Size: `PrimitiveInt` width/height × `Float` "Resolution Multiplier" (1.0),
  computed with `SimpleMath+` (essentials), then `EmptyLatentImage`, batch 1.
  Compute width × multiplier in Python; don't install essentials for this.
  The defaults are 1080 × 1920 in the Turbo file and 1140 × 1472 in the
  Upscale file.
- `KSampler` (204): **12 steps, CFG 1, `er_sde`, scheduler `simple`,
  denoise 1**. Seed is randomised in the file; the tab uses its own seed
  field.
- `VAEDecode` (8), then a preview. The app uses `SaveImage` with its own
  prefix, the way every other tab does.
- The resolution note in both files lists these (w × h): 1:1 1328×1328,
  16:9 1664×928, 9:16 928×1664, 4:3 1472×1140, 3:4 1140×1472,
  3:2 1584×1056, 2:3 1056×1584, plus 1080p: 1080×1920 and 1920×1080.

**Upscale pass (Upscale file only), all fixed values:**

- `UpscaleModelLoader` (210): `4xLSDIR.pth`.
- `UltimateSDUpscale` (205), with inputs `image` (the decoded base image),
  `model` (the same LoRA-patched model as the base pass), `positive` and
  `negative` (the same conditioning), `vae`, `upscale_model`, and
  `tile_width` / `tile_height` **set to the base width/height**. Its widgets
  in order are: `upscale_by 1.5`, seed, `randomize`, `steps 8`, `cfg 1`,
  `er_sde`, `simple`, `denoise 0.18`, `mode_type "Chess"`, `tile_width
  1024`, `tile_height 1024`, `mask_blur 8`, `tile_padding 32`,
  `seam_fix_mode "None"`, `seam_fix_denoise 1`, `seam_fix_width 64`,
  `seam_fix_mask_blur 8`, `seam_fix_padding 16`, `force_uniform_tiles
  true`, `tiled_decode false`. The two 1024 tile widgets are overridden by
  the linked inputs. Map widget names to values against the pack's
  `INPUT_TYPES` at the pinned commit, not by position alone.
- `ToBasicPipe` / `FromBasicPipe_v2` (Impact Pack) and the `Reroute`s are
  wiring only; leave them out.
- Seed for the upscale pass: use the job's seed.
- Output: with upscale **on**, save only the upscaled image. With upscale
  **off**, the graph must not contain the upscale nodes at all, so it is
  exactly the Turbo workflow.

The only custom node pack the transcribed graph needs is
**ComfyUI_UltimateSDUpscale**, and only when the tick is on. Every other
node is core ComfyUI.

## 4. Weights

| File | ComfyUI dir | Source | Size |
| --- | --- | --- | --- |
| `z_image_turbo_bf16.safetensors` | `diffusion_models` | HF `Comfy-Org/z_image_turbo`, `split_files/diffusion_models/…` | 12.3 GB |
| `qwen_3_4b.safetensors` | `text_encoders` | HF `Comfy-Org/z_image_turbo`, `split_files/text_encoders/…` | 8.0 GB |
| `ae.safetensors` | `vae` | HF `Comfy-Org/z_image_turbo`, `split_files/vae/ae.safetensors` (**not** the template's `modelzpalace/ae.safetensors`, which is a community copy) | 0.3 GB |
| `4xLSDIR.pth` | **`upscale_models`** | HF `Hearmeman/comfyui-template-assets`, `upscale_models/4xLSDIR.pth` | 0.07 GB |

- The **diffusion model is a catalogue model record**, so the Model dropdown
  works like Krea's (§6). The text encoder, VAE and upscaler are fixed
  pipeline weights in `ember/pipelines/zimage/constants.py`, fetched by a
  new asset group.
- Revisions to pin in `scripts/PINS.json`:
  - `Comfy-Org/z_image_turbo` → `6fc90a3b1b653e935a0d175e260736de25b84df5`
  - `Hearmeman/comfyui-template-assets` → `75884efd87e34b798899d6930ab3b4a9e6cf8fb3`

  Check both are still the current SHAs; if they've moved, pin what you
  verified.
- `Hearmeman/comfyui-template-assets` is a community repo, so per
  `docs/releasing/mirror-and-pins.md` it should get a `mirror_manifest.json`
  item. Add the manifest entry, but **don't upload anything**. List the
  upload as a follow-up for the user.
- `ember/comfy/setup.py` `MODEL_DIRS` does **not** include
  `upscale_models` today. Add it so `link_model_dirs()` exposes the folder
  to ComfyUI. Check that this changes nothing for existing tabs, and check
  `scripts/mock_models.py` and the dry run for anything else that lists
  model dirs.

## 5. Code touch points

`docs/development/adding-a-pipeline.md` is the checklist. Follow it in its
order, and use the MiniMax tab's commits as the worked example
(`git show --stat c33e076 b52b79f bf6ea64`). Also
`git grep -n "krea_t2i\|KREA_T2I\|minimax_i2v\|TAB_MINIMAX"` and decide,
spot by spot, whether Z-Image belongs there. Expect at least:

1. **`ember/pipelines/zimage/`**: an empty `__init__.py`; `constants.py`
   (files, repos, node pack tuple `(dirname, url, class_type)`, resolution
   table and default, samplers, default negative, upscale constants); and
   `workflow.py` with `build_zimage_workflow(job)` returning the API-format
   dict. Reuse `ember/pipelines/common.py` and the Krea LoRA-chain helper
   where it fits. Anything you share goes into `common.py`, never imported
   sideways from `krea2/`.
2. **`ember/pipelines/zimage/handler.py`**: `generate_zimage(prompt,
   negative, seed, randomize, steps, cfg, resolution, multiplier, sampler,
   model, upscale, batch_count, save_preset, preset_name, *lora_slots)`, or
   an equivalent order. The order must match the schema exactly
   (`_assert_signatures()` enforces this at import). Model it on
   `generate_single`:
   - `_check_model`, and a clear error if the text encoder, VAE or upscaler
     is missing.
   - With upscale on, a clear error if the UltimateSDUpscale nodes aren't
     registered.
   - `_save_preset(presets.TAB_ZIMAGE, …)` with an id-based settings blob:
     model, resolution, multiplier, steps, cfg, sampler, seed, randomize,
     batch_count, upscale, and `loras` as triples.
   - `_resolve_lora_slots`, then `_run_jobs(jobs,
     builder=build_zimage_workflow, prefix="ZImage")`.
   - **Don't** push sizes through Krea's `parse_resolution` / `_snap`:
     that rounds 1080 to 1088. Use the tab's own table, apply the
     multiplier, and check what the pinned ComfyUI accepts (EmptyLatentImage
     steps in 8) before deciding on rounding.
3. **`ember/web/schema/tabs/zimage.py`**: `ZIMAGE_SCHEMA`, based on
   `KREA2_SCHEMA`.
   - Groups: `G_PROMPT, G_CORE, G_SAMPLER`, a collapsible
     **"Upscale"** group (or reuse V2's `post` group pattern), `G_SEED`,
     `G_SAVE_PRESET`.
   - Fields: prompt (the default is a short, SFW example), negative (the
     workflow's negative text, collapsed, with `_CFG_NOTE`), `_seed_fields()`,
     steps (model setting), cfg (model setting), resolution select
     (default 1080×1920), a resolution-multiplier slider (0.5–2.0, default
     1.0) in Output, sampler select (default `er_sde`),
     `_model_field(handlers.ZIMAGE_T2I)`, the `upscale` bool (default
     `False`, label "Upscale 1.5x (UltimateSDUpscale + 4xLSDIR)"),
     `_batch_field()`, `_preset_save_fields()`, and the `lora_slots`
     `_blank_lora_tail`.
   - Register it in `SCHEMAS` in `ember/web/tabschema.py`, and extend
     anything there (`catalog()`, `_model_rows`) that has to know the tab's
     resolution list.
4. **`ember/generation/handlers.py`**: `ZIMAGE_T2I = str(Key.ZIMAGE_T2I)`
   next to the Krea registries.
5. **`ember/features.py`**:
   `Key.ZIMAGE_T2I = "zimage_t2i"` and
   `Feature(Key.ZIMAGE_T2I, "⚡ Z-Image", needs=("zimage", "catalog"))`.
   `default=False`, `enabled=True`.
6. **`ember/weights/downloads.py`**: `download_zimage_models()` (the text
   encoder, VAE and upscaler at their pins) and `ASSET_GROUPS["zimage"]`.
   The diffusion model comes through the existing `catalog` group.
7. **`ember/comfy/setup.py`**: install the UltimateSDUpscale pack only when
   `zimage_t2i` is enabled, following `install_v2_nodes`'s contract (log,
   don't raise).
   - The pack has a **git submodule** (`repositories/ultimate_sd_upscale`).
     `clone_pinned()` / `repin_checkout()` don't init submodules today, so
     make sure the submodule is populated, and change nothing for packs
     without `.gitmodules`.
   - Pin it in `PINS.json`: `ComfyUI_UltimateSDUpscale`, url
     `https://github.com/ssitu/ComfyUI_UltimateSDUpscale`, sha
     `a5547db9e1d07d3318bb21e9e9c474f4c1e9c8df` (June 2026 main; check it).
   - It has no mirror tarball yet, so the pinned clone is the path taken.
   - Add a `node_packs` manifest entry if the manifest expects one, but
     don't upload.
8. **`ember/main.py`**: `comfy.verify_custom_node("UltimateSDUpscale", …)`
   when the feature is on, like the V2 and Krea2Edit checks.
9. **`ember/licensing/presets.py`**: `TAB_ZIMAGE = str(Key.ZIMAGE_T2I)` and
   add it to `TABS`. Leave `prompts.py` alone (no prompt library).
10. **The Docker bake stage**: check whether it imports anything new
    (`Dockerfile` COPY lines and the `.dockerignore` allow-list). It
    probably doesn't; confirm it.
11. **The front end**: the tab should render through the existing
    schema-driven `GenerateTab` with **no React changes**, the same way the
    MiniMax tabs did. If you do have to touch `webui/`, regenerate the
    bundle with `scripts/gen_webui_bundle.py` and commit it, as `bee6de3`
    did.
12. **`assets/showcase/showcase.json`**: the pricing page only describes
    tabs that are on a plan. Add an entry only if a check requires one, and
    say which.

## 6. The licence server and the production DB

**Seed files in the repo** (they document what the DB holds):

- `license-validator/src/features.js`: a `FEATURES` entry with
  `key: "zimage_t2i"`, `name: "Z-Image Turbo"`, `tab_label: "⚡ Z-Image"`,
  a one-line description, `category: "generation"`, and a `sort_order`
  that places it after Krea2 V2 (for example 25). It needs to be here so
  `npm run issue-key -- --key … --features-extra zimage_t2i --update`
  accepts the key.
  - **`enabled`**: read what the flag controls in `features.js` and
    `plans.js`. The tab must be deliverable to a key that has it in
    `features_extra`, including its tab label, and it must not appear
    anywhere customers see (pricing page, plans and features page).
    Choose the value that achieves both, verify it, and explain the choice.
- `license-validator/data/assets.json`:
  - A model record `z-image-turbo-bf16`: name "Z-Image Turbo bf16", the
    file, source `{kind: "hf", repo: "Comfy-Org/z_image_turbo", path:
    "split_files/diffusion_models/z_image_turbo_bf16.safetensors"}`,
    `mirror: null`, `variant: "turbo"`, `steps: 12`, `cfg: 1.0`,
    `turbo_lora: null`, `trigger: ""`, `enabled: true`.
  - `features.zimage_t2i = { models: ["z-image-turbo-bf16"], loras: [] }`.
  - Confirm that an empty `loras` list passes `idListProblems()` /
    `emptyModelsProblem()`.
- `license-validator/src/app.js` `PRESET_TABS`, and the `PRESET_TABS`
  lists in `scripts/presets.js` / `scripts/seed-presets.js`: add
  `zimage_t2i` so presets can be saved.
  - Also check the unfiltered `GET /v1/presets` path (around the
    `PRESET_TABS.includes(req.query.tab)` lines) and make sure the pod
    only ever shows Z-Image presets on this tab.
  - Saving from the app only works once the user redeploys the server.
    Say so in the report; **don't deploy**.
- **Don't touch `plans.js`.**

**Production (Atlas)**, written with a **targeted script, never
`seed-catalog` / `seed-assets` / `seed-presets`**. Those `$set` whole
collections from seed files that may be stale: the DB is the source of
truth, and `seed-catalog` would also rewrite every plan.

1. Back up first: dump the current `features`, `models` and
   `feature_assets` collections as EJSON into
   `tmp/zimage-db-<yyyymmdd>/` (git-ignored) with a `backup.mjs`. Earlier
   precedents are `tmp/minimax-loras-20260919/` and
   `tmp/minimax-preset-20260924/`; copy their style. Connection settings
   are in `license-validator/.env`.
2. `add.mjs`: insert the `features` row (`_id: "zimage_t2i"`), the
   `models` row (`_id: "z-image-turbo-bf16"`) and the `feature_assets` row
   (`_id: "zimage_t2i"`). Each insert must **fail rather than overwrite**
   if the `_id` already exists. Run every document through the same
   validators the server uses (`src/assets.js`) before writing it.
3. Read back and print what was written. Check `plans` is untouched and
   that no plan's `features` contains `zimage_t2i`.
4. Write `rollback.mjs`, which deletes exactly those three `_id`s, but
   don't run it.
5. **Ask the user before step 2**, showing the three documents. It is a
   production write.

## 7. Docs

Update the docs as part of the work, not as an afterthought. Every place
that lists tabs, features, keys, asset groups, node packs, pins or
download sizes needs the new tab. At least:

- a new `docs/pipelines/zimage.md` in the style of `krea2.md` /
  `minimax.md`: what the tab does, the graph and its fixed values, the
  upscale pass, the weights and sizes, the node pack, and what's
  deliberately left out (JPS, SimpleMath, BasicPipe, ModelPassThrough);
- `docs/pipelines/catalogue.md`;
- `docs/architecture/licensing-and-features.md`: the feature table,
  marked as on no plan yet;
- `docs/development/adding-a-pipeline.md`, if anything you learned
  (submodules, `upscale_models`) belongs in the checklist;
- `docs/releasing/mirror-and-pins.md`, for the new pins and manifest
  entries;
- `docs/reference/commands.md`, if you added commands;
- `license-validator/docs/plans-and-entitlements.md` (the feature key
  table) and `license-validator/docs/catalogue-data.md`: the model record,
  the empty LoRA list, and the new preset tab;
- `license-validator/README.md` and the root `README.md` / `docs/README.md`
  wherever they list tabs, counts or sizes;
- `docs/features/presets.md`, for the new preset tab.

Fix any counts ("nine tabs", route counts and so on) that the new tab
changes. `scripts/check_docs.py` has to pass. Match the existing docs'
tone: say what it is and why, with no marketing and no changelog-style
history notes.

## 8. Verification (all of it, before committing)

Use the local **`krea2` conda env** (`%USERPROFILE%\miniconda3\envs\krea2\python.exe`),
not Docker.

```bash
$PY scripts/check_imports.py
$PY scripts/check_config.py        # update config_baseline.json only for NEW constants
$PY scripts/check_routes.py        # the new tab's routes 403 without the feature
$PY scripts/check_schema.py --choices   # parity_baseline.json only gains a zimage section
$PY scripts/check_webui.py
$PY scripts/check_docs.py
$PY scripts/golden.py --check      # every EXISTING golden unchanged
cd license-validator && npm test   # if a test script exists; otherwise say so
```

- **Goldens**: add cases for `generate_zimage` with upscale **off** and
  **on**, generate them, and commit `scripts/golden/generate_zimage*.json`.
  Existing goldens must not change at all; if one does, you broke something.
- **Graph validation against real ComfyUI**: `tmp2/ComfyUI` is a checkout
  with every model mocked as zero-byte files (see
  `scripts/mock_models.py`).
  - Add zero-byte mocks for the four new files, and the UltimateSDUpscale
    pack at the pin (with its submodule).
  - Start that ComfyUI on a **spare port** (for example 8190, never 7860),
    POST both goldens to `/prompt`, and confirm they pass validation. That
    also checks `CLIPLoader` type `qwen_image` and the UltimateSDUpscale
    input names.
  - Stop it afterwards.
- **Dry run**: `$PY scripts/dryrun.py --features all --port 7870`. **Never
  port 7860**: the user runs the real app there. Open the tab (puppeteer
  with the installed Chrome works; tick the 18+ box on the terms screen
  first). Check that:
  - the layout matches Krea2: sections on the left, result on the right,
    LoRA stack at the bottom;
  - the Upscale box is unticked by default;
  - the Model dropdown shows Z-Image Turbo bf16 with steps 12 / CFG 1;
  - the LoRA rows are present with an empty list;
  - a submit reaches the worker and fails cleanly with "not downloaded".

  Also make sure the other tabs still render and submit. Stop the dry run
  afterwards.
- A real GPU render isn't needed; the user tests on RunPod.

## 9. Commit

- Commit on **`63-zimage`** in logical steps: the licence-server seed files;
  the app pipeline + schema + downloads + setup; checks, goldens and
  baselines; docs. Use messages in the repo's style (see `git log`), each
  ending with the attribution line your session is configured with.
- Don't commit `tmp/`, `debug.log`, the unrelated `.gitignore` edit already
  in the working tree, or anything else under `prompts/`.
- **Don't push, deploy, build or release.**

## 10. Final report

Keep it short and concrete:

- what changed, by area;
- how the upscale tick maps onto the graph;
- the `CLIPLoader` type you ended up with, and why;
- your `enabled` choice for the feature row, and why;
- each check with its result, with the output of any failure;
- the production DB write: what was written, where the backup and
  rollback are, or that it's waiting for approval;
- follow-ups for the user:
  1. redeploy the licence server (needed for saving Z-Image presets and for
     the new `PRESET_TABS`);
  2. grant the key: `npm run issue-key -- --key <key> --features-extra
     zimage_t2i --update`;
  3. restart the pod so it downloads about 20.7 GB;
  4. optionally mirror `4xLSDIR.pth` and the UltimateSDUpscale tarball to
     HF;
  5. later, add the feature to a plan and add Z-Image LoRAs.
