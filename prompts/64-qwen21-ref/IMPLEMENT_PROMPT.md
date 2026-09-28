# Add a Qwen 2.1 Reference tab (1–10 reference images)

You are working in the Ember repo (the `ember/` package, the React app in
`webui/` and the licence server in `license-validator/`). Implement one new
editing tab, **🧩 Qwen 2.1 Reference**. The user writes a prompt and adds
1 to 10 numbered reference images, and Qwen Image 2.1 makes one new image
that keeps them consistent.

Work on branch **`64-qwen21-ref`**, created from `63-zimage`. The Z-Image
tab lives there and isn't merged yet, so build on it.

**Read these first, in this order.** They are the spec:

1. `prompts/64-qwen21-ref/RESEARCH.md`: the workflow, its graph, the two
   new core nodes, the weights, and why the template's node pack is
   skipped.
2. `prompts/64-qwen21-ref/mockup/qwen21-reference.html`: the **approved UI
   mockup**. Open it in a browser and go through every state, both layouts,
   both themes and phone width. **The UI you build must match it**, except
   where §2 below says otherwise.
3. `prompts/63-zimage/PROMPT.md` and the Z-Image commits on this branch
   (`git log --oneline 62-min..63-zimage`). They are the closest precedent
   for every touch point: feature key, catalogue, DB rows, presets, docs,
   goldens.
4. `docs/development/adding-a-pipeline.md`: the checklist.

If anything here contradicts the code, trust the code, say so in the
report, and choose the option that changes existing behaviour least.

---

## 1. Hard rules

- **Nothing that works today may break.** Every existing tab must look
  and behave the same after your change, at every width, in both themes.
  Existing goldens stay byte-identical.
- **Deploy nothing.** No licence-server deploy, no build or release, no
  pod, no HF/R2 uploads, no push. The user does all of that by hand.
  (Pushes from this machine fail with a 403 anyway.)
- **The only production write** is the DB insert in §6, after a backup
  and after asking the user.
- **On no plan**, not even `admin`. The user grants it to their own key
  with `features_extra`.
- **Update the docs properly** (§7).
- Test in the local **`krea2` conda env**
  (`%USERPROFILE%\miniconda3\envs\krea2\python.exe`), not Docker. Fresh
  venvs need `--index-url https://pypi.org/simple`, because the global pip
  points at a private feed. Run dry runs on **port 7870, never 7860**, and
  stop them when you're done.

## 2. Decisions already made (don't re-ask)

| Topic | Decision |
| --- | --- |
| Feature key | `qwen21_edit` (following `krea_edit`). The key is permanent once it ships. |
| Tab | `🧩 Qwen 2.1 Reference`, icon `🧩`, `category="edit"`, route `/edit/qwen21-reference`, `tab_id="qwen21ref"`, `submit_label="Generate"`. In `SCHEMAS` right after the Krea2 V2 Edit tab. |
| Layout | Follow the mockup. **Layout A on wide screens**: references in the right column above the result, like Krea2 Edit's Images. **Layout B when the page stacks** (phone): references first, above the prompt. See §5.3. |
| Output size | A select, **default "Same as reference 1"**, then the workflow's presets: 1:1 2048×2048, 4:3 2400×1792, 3:4 1792×2400, 3:2 2528×1696, 2:3 1696×2528, 16:9 2752×1536, 9:16 1536×2752. |
| Mentions line | **Not now.** Leave out the mockup's "Mentions" line and the offer to rewrite the prompt on reorder (marked "Proposal · optional"), and every annotation about them. |
| Per-image rotate / mirror / crop | Not in the approved mockup, so don't build them. |
| Node pack | None. Use core `LoadImage` per reference and do the preprocessing in Python (RESEARCH.md, "The custom node pack: skip it"). |
| LoRAs | A stack backed by an empty catalogue list for `qwen21_edit`. The workflow's LoRA loader patches **model and CLIP**, so use the model + CLIP stack, like Krea2 V2. |
| Presets | Save-preset only, under preset tab `qwen21_edit`, like Z-Image. No prompt-library publish. Reference images are **never** stored in a preset, the same as images on the other tabs. |
| Model | A catalogue model record, `qwen-image-2-1-int8`. |

## 3. Phase 1: upgrade ComfyUI first, in a commit of its own

The workflow's two core nodes, `TextEncodeQwenImage21` and
`QwenImage21Cache`, and the Qwen 2.1 model itself first appear in **ComfyUI
v0.37.0**. The app pins **v0.34.0** (`scripts/PINS.json`, `12d5279…`).
That pin runs every tab, so upgrading it is the risky part of this job. Do
it first, prove nothing broke, and commit it **before** writing any
Qwen 2.1 code.

1. Move the `ComfyUI` pin to **v0.37.4** = `8ff6dc384ba5c410266b40e137799e049459d4f2`.
   - Check that it's still the latest v0.37.x. If there's a newer patch
     release, pin that and say so.
   - If v0.37.x turns out to be broken for us, fall back to the lowest
     release that has both nodes.
2. Read ComfyUI's changes between v0.34.0 and the new pin for anything
   that affects us: the API format, `/prompt` validation, node renames,
   and the `requirements.txt` changes. v0.37.4 adds `comfy-kitchen`,
   `comfy-aimdo`, a new `comfyui-frontend-package` and `av>=17`. Check
   whether any of these fight the torch constraint in
   `ember/comfy/setup.py` (`torch_constraints_file()`), the Docker image's
   torch/CUDA/Sage pins (see the MiniMax v8 pin commit, `4e17bec`) or the
   Windows install path.
3. Update `tmp2/ComfyUI` (the mocked-weights checkout) to the new pin and
   install its requirements in the `krea2` env. Then start it on a spare
   port (for example 8190) and check that:
   - **every existing golden** in `scripts/golden/*.json` still passes
     `/prompt` validation: Krea2, Krea2 Edit, V2, V2 Edit, Wan, both
     MiniMax cases and both Z-Image cases;
   - every custom node pack the app installs still loads: RES4LYF, RBG
     SmartSeedVariance, post-processing, comfyui-krea2edit and
     ComfyUI_UltimateSDUpscale (`main.py`'s `verify_custom_node` checks
     are the list);
   - the MiniMax core-node check (`MINIMAX_COMFYUI_MIN = "v0.34.0"`) still
     holds;
   - the Z-Image LoRAs still load. One is an ai-toolkit **LoKr**
     (`yukes-body-v1-0`), and on 2026-09-27 all 240 of its keys were
     confirmed to map with ComfyUI 12d52794's loader. Repeat that
     key-mapping check on the new pin, because LoRA loaders change
     between releases.
4. Check that `repin_checkout()` moves an existing v0.34.0 checkout to the
   new pin: that's how customer volumes get upgraded.
5. If a small real render is practical on the local RTX 5050 (the real
   Krea 2 weights are in `tmp/`), run one Krea2 generation on the new pin
   as a smoke test.
6. **If any of this fails and the fix isn't small and obviously safe,
   stop.** Report what broke instead of pressing on. Otherwise commit the
   upgrade by itself: "Pin ComfyUI at v0.37.x for Qwen Image 2.1", in the
   style of `7c20868`, with the docs that mention the pin or version
   updated in the same commit.

## 4. Phase 2: the pipeline

`ember/pipelines/qwen21/` contains:

- an empty `__init__.py`;
- `constants.py`: files, repos, the output-size table, the default
  reference detail (1024), samplers, schedulers (`simple`, `beta`), the
  fixed cache options (`auto`, `default`), and the reference limits (max
  10, longest edge 2048);
- `workflow.py`: `build_qwen21_ref_workflow(job)`;
- `handler.py`: `generate_qwen21_ref(...)`.

**The graph** (the workflow and RESEARCH.md have the full detail):

```
UNETLoader(qwen_image_2.1_int8_convrot) ─┐
CLIPLoader(qwen3vl_8b_int8_convrot, type qwen_image) ─┤
                                          └─ LoRA chain (model + clip) ─ QwenImage21Cache(auto, default) ─ KSampler
VAELoader(qwen_image_2.1_vae_bf16)
LoadImage ×N ─ TextEncodeQwenImage21(clip, prompt, negative_prompt, vae, resolution, images.image_1..N)
                 └─ positive / negative ─ KSampler(steps, cfg, sampler, scheduler, denoise 1)
EmptyLatentImage(width, height, 1) ─ KSampler ─ VAEDecode ─ SaveImage(prefix "Qwen21Ref")
```

- Wire only as many `images.image_N` inputs as there are references,
  numbered 1..N in the user's order. Check the Autogrow input's API-format
  names against `/object_info` on the new pin.
- **Size.** For a preset, use its exact width × height. For "Same as
  reference 1", use reference 1's aspect ratio at about 2048² pixels,
  rounded to what the latent needs (the node uses multiples of 32; check
  what `EmptyLatentImage` and the model accept). Don't use the encoder's
  own `latent` output: it's sized to `resolution` (1024²), not to 2048².
  Put the formula in one place and send it to the browser too, through
  the tab's catalogue data, so the hint under the select ("Output will be
  1536 × 2048, the shape of reference 1") shows the size the pod will
  actually use.
- **Reference preprocessing** in the handler, before upload:
  - `ImageOps.exif_transpose`;
  - composite RGBA onto white (as the node does for the vision tower);
  - cap the longest edge at 2048 with LANCZOS (the template node's
    `max_reference_edge`);
  - upload each with `client.upload_image` under a unique name, the way
    `generate_edit` does.
- **Handler guards:**
  - no references → "❌ Add at least one reference image.";
  - more than 10 → refuse;
  - an empty prompt → refuse;
  - model, text encoder or VAE not downloaded → the usual messages;
  - the two core nodes missing (ComfyUI too old) → a clear message.
- The settings blob for presets: model, output size, reference detail,
  steps, cfg, sampler, scheduler, seed, randomize, batch_count and the
  LoRA triples. **No images.**
- Run jobs with `_run_jobs(jobs, builder=build_qwen21_ref_workflow,
  prefix="Qwen21Ref")`, one image per job and `batch_count` jobs on
  sequential seeds, like the other tabs.

## 5. Phase 2: the multi-image field and the tab

### 5.1 A new field type, `images`

Today there's only a single `image` field (`ImageDropField` in
`webui/src/components/fields/index.tsx`). Add a general **`images`** field
type (a list, with a `max`) rather than a one-off, and carry it through
every layer that knows about `image`. Find them all with
`git grep -n "'image'\|\"image\"\|kind == \"image\"" -- ember webui/src`.
At least:

- **The schema model** (`ember/web/schema/model.py`): the kind, its
  `max`, its JSON, and how the route turns upload ids into PIL images.
  The handler receives **one positional argument, a list of PIL images**,
  in the user's order. `_assert_signatures()` must still pass: one field,
  one parameter.
- **Routes** (`ember/web/routes/common.py` ~l.181–199, `tabs.py` ~l.118):
  resolve a list of upload ids, validate the count, and refuse non-images.
- **Recipes and the gallery's "Load these settings"**
  (`ember/generation/recipes.py`, `webui/src/features/gallery/Lightbox.tsx`
  `withSources()` ~l.520): a finished image's recipe records its references
  so they come back in order, the same way single image sources are
  handled today. Any that can't be fetched are dropped quietly, as now.
- **Submit** (`webui/src/api/http.ts` `resolveUploads()`): upload each file
  and send the list of ids.
- **Types** (`webui/src/api/types.ts` `FieldType`), `SchemaForm`
  (`case 'images'`), and tab state (`webui/src/store/tabState.ts`), so the
  references survive switching tabs the same way a single image does now.
- `scripts/check_schema.py` / `parity_baseline.json`, `check_webui.py`,
  and anything else that switches on field types.

### 5.2 The field's UI: build it as the mockup shows

Reuse the existing building blocks: `Button`, the drop-zone styles, the
size chip, `RecentStrip`, and the gallery `Lightbox`. Use tokens only, no
colour literals. The mockup shows:

- **Empty:** one big drop zone: "Drop up to 10 images, or click to
  choose · Pick several at once · Ctrl+V pastes from the clipboard".
  Generate is disabled.
- **Filled:** a grid of cover-cropped tiles (5 columns at desktop, fewer
  as it narrows), each with:
  - a **number badge 1–10**;
  - tile 1 carrying a **"Main"** chip;
  - a **W × H** chip;
  - hover/focus controls: remove (×) and a drag handle.
- **An Add tile** at the end with a count ("3 / 10"). It disappears at 10.
  It takes multi-select, multi-file drop and paste.
- **Reordering:** drag, plus the keyboard **Alt+← / Alt+→** on a focused
  tile. The numbers update as you drop (state 5, but without the Mentions
  rewrite).
- **Clicking a tile** opens it full size in the existing Lightbox.
- **The Recent generations strip** underneath: a click adds that picture
  as the next reference.
- **Refusals shown inside the field**, next to what they're about, not as
  a toast (state 7): a non-image is skipped, and an 11th image is refused
  with "Up to 10 references". The rest of the drop still goes in.
- **The drop-active highlight** while files are dragged over (state 4).
- **Accessibility:** real buttons with labels, visible focus rings, and
  number badges that don't rely on colour.

### 5.3 Layout A on desktop, B when stacked

The mockup's note says: "Both at once needs TwoColumn to lift the right
column's inputs to the top when it stacks." Implement exactly that, and
make it **opt-in**:

- Add a schema flag (on the group or the tab, for example
  `stack_first=True` on the references group) that `TwoColumn` /
  `GenerateTab` use to put that group first when the columns stack.
- Only this tab sets it. **Every other tab must render exactly as
  before**: compare before/after screenshots of all tabs at 1440, 1024 and
  390 px, in both themes.

### 5.4 The tab schema

`ember/web/schema/tabs/qwen21.py` defines `QWEN21_REF_SCHEMA`. Use the
mockup's section order and labels:

| Section | Fields |
| --- | --- |
| References (`G_INPUTS`-style, right column, stack first) | `references`, the new `images` field, label "Reference images (paste with Ctrl+V)", `max=10` |
| Prompt | prompt (the mockup's sample text uses image numbers, e.g. "The woman from image 1 wearing the leather jacket from image 2, …"); negative (collapsed, `_CFG_NOTE`) |
| Output | output size (default "Same as reference 1", with the live hint); model (`_model_field`) |
| Sampler (collapsible) | steps 25 (1–60), CFG 1.0 (0.5–8), sampler `euler` (with the other samplers the mockup lists), scheduler `simple` / `beta` |
| Advanced (collapsible, closed; marked "new" in the mockup) | reference detail 1024 (0–4096, step 32), hint "How finely each reference is read. 0 keeps each image's own size." |
| Seed & batch | `_seed_fields()`, `_batch_field()` |
| Presets | `_preset_save_fields()` |
| LoRA stack | the model + CLIP variant, bottom |

Register it in `ember/web/tabschema.py` and extend `catalog()` for the
output-size table and size formula.

## 6. Features, downloads, the licence server and the DB

Copy the Z-Image precedent everywhere:

- **`ember/features.py`:** `Key.QWEN21_EDIT = "qwen21_edit"` and
  `Feature(Key.QWEN21_EDIT, "🧩 Qwen 2.1 Reference", needs=("qwen21",
  "catalog"))`, with `default=False`.
- **`ember/generation/handlers.py`:** `QWEN21_EDIT` registry constant.
- **`ember/weights/downloads.py`:** `download_qwen21_models()`, which
  fetches the text encoder and VAE at the pin, and
  `ASSET_GROUPS["qwen21"]`. The diffusion model comes through `catalog`.
- **`scripts/PINS.json`:** `Comfy-Org/Qwen-Image-2.1` →
  `9a44dbdb47cefd046be9c0a13476192f34c8db8e`. Check it's still current.
  It's org-backed, so it needs no mirror.
- **`ember/main.py`:** `comfy.verify_core_node("TextEncodeQwenImage21",
  "v0.37.0")` when the feature is on, like MiniMax's check.
- **`ember/licensing/presets.py`:** `TAB_QWEN21 = str(Key.QWEN21_EDIT)`,
  added to `TABS`.
- **`license-validator/src/features.js`:**
  - key `qwen21_edit`, name "Qwen 2.1 Reference", `tab_label` "🧩 Qwen 2.1
    Reference", category `editing`, a `sort_order` after Krea2 V2 Edit;
  - **`enabled: false`**, like Z-Image: that hides it from `/v1/plans` and
    the bot, while acquire still delivers it and its label.
- **`license-validator/data/assets.json`:**
  - model `qwen-image-2-1-int8`: name "Qwen Image 2.1 int8", source
    `{kind: "hf", repo: "Comfy-Org/Qwen-Image-2.1", path:
    "diffusion_models/qwen_image_2.1_int8_convrot.safetensors"}`,
    `mirror: null`, `variant` from `MODEL_VARIANTS` (`raw` fits: 25 steps,
    not distilled), `steps: 25`, `cfg: 1.0`, `turbo_lora: null`;
  - `features.qwen21_edit = { models: ["qwen-image-2-1-int8"], loras: [] }`.
- **`PRESET_TABS`** in `license-validator/src/app.js`,
  `scripts/presets.js` and `scripts/seed-presets.js`: add `qwen21_edit`.
  Saving from the app works only after the user redeploys the server.
- **Don't touch `plans.js`.**
- **Production DB:** follow `tmp/zimage-db-20260927/` exactly.
  1. Back up `features`, `models`, `feature_assets` and `plans` as EJSON
     to `tmp/qwen21-db-<yyyymmdd>/`.
  2. `add.mjs` inserts the three rows. It fails rather than overwrites if
     an `_id` exists, and validates through `src/assets.js`.
  3. Read back the rows, and check that plans are unchanged.
  4. Write `rollback.mjs`, which deletes exactly those three `_id`s, but
     don't run it.
  5. Never run `seed-catalog`, `seed-assets` or `seed-presets` against
     prod.
  6. **Show the user the three documents and ask before running
     `add.mjs`.**

## 7. Docs

Every place that lists tabs, features, keys, asset groups, field types,
pins, ComfyUI versions or download sizes gets updated, in the existing
tone. That means no marketing and no history notes. Fix any counts the new
tab changes. At least:

- a new **`docs/pipelines/qwen21.md`**, in the style of
  `docs/pipelines/zimage.md`: the graph, the size rule, preprocessing,
  weights (≈ 17.4 GB), and what's deliberately left out (the RefPack node,
  Mentions);
- `docs/pipelines/catalogue.md`;
- `docs/architecture/licensing-and-features.md` (the feature table, on no
  plan);
- `docs/architecture/web-ui.md`: the new `images` field type and the
  stack-first layout flag;
- `docs/architecture/gallery-and-recipes.md`: references in recipes;
- `docs/development/adding-a-pipeline.md`, if you learned anything that
  belongs in the checklist;
- `docs/releasing/mirror-and-pins.md`: the ComfyUI pin and the new HF pin;
- `docs/features/presets.md`;
- `license-validator/docs/plans-and-entitlements.md` and
  `catalogue-data.md`;
- `license-validator/README.md`, the root `README.md` and `docs/README.md`,
  wherever they list tabs or sizes.

`scripts/check_docs.py` must pass.

## 8. Verification (all of it, before the final commits)

```bash
$PY scripts/check_imports.py
$PY scripts/check_config.py
$PY scripts/check_routes.py        # the new tab's routes 403 without the feature
$PY scripts/check_schema.py --choices   # parity_baseline.json only gains the new section + field type
$PY scripts/check_webui.py
$PY scripts/check_docs.py
$PY scripts/golden.py --check      # all existing goldens unchanged
cd webui && npm test && npm run build   # whatever the webui's scripts are
```

- **Goldens:** add `generate_qwen21_ref` cases for 1 reference with
  "Same as reference 1", 3 references with a 16:9 preset, and 10
  references. Commit them, and validate all three with `/prompt` against
  the new-pin `tmp2/ComfyUI`, adding zero-byte mocks for the three new
  weight files.
- **Dry run:** `$PY scripts/dryrun.py --features all --port 7870`.
  Use puppeteer-core with the installed Chrome, and tick the 18+ box on
  the terms screen first.
  - Screenshot the new tab in mockup states 1–5 and 7 in the real app,
    plus the size hint (state 6), at 1440 and 390 px in both themes.
    Compare them with the mockup side by side and fix any differences.
  - Submit with 3 references: the job reaches the worker and fails
    cleanly with "not downloaded".
  - Check that **every other tab** looks exactly the same as before at
    1440 and 390 px (screenshots from before your change), and that a
    Krea2 Edit single-image upload still works.
  - Stop the dry run afterwards.
- **No real GPU render of Qwen 2.1.** 17 GB won't fit the local RTX 5050,
  so the user tests it on RunPod.
- Regenerate the React bundle with `scripts/gen_webui_bundle.py` and
  commit it, as `bee6de3` did.

## 9. Commits

These go on `64-qwen21-ref`, in this order, in the repo's message style,
each ending with the attribution line your session is configured with:

1. the ComfyUI pin upgrade (§3), on its own, already verified;
2. the licence-server seed files;
3. the `images` field type and the stack-first layout flag (backend +
   frontend), with no tab using them yet, and all other tabs unchanged;
4. the Qwen 2.1 pipeline and tab;
5. goldens, the parity baseline and config baselines;
6. docs;
7. the regenerated React bundle.

Don't commit `tmp/`, `debug.log`, the unrelated `.gitignore` edit or
anything else under `prompts/`. **Don't push or deploy.**

## 10. Final report

- what changed, by area;
- the ComfyUI version you pinned, what you checked, and anything that
  needed adjusting;
- the output-size formula, with one worked example;
- each check with its result, with the output of any failure;
- the DB write: what was written, where the backup and rollback are, or
  that it's waiting for approval;
- follow-ups for the user:
  1. push `63-zimage` and `64-qwen21-ref`;
  2. redeploy the licence server (for presets);
  3. grant the key: `npm run issue-key -- --key <key> --features-extra
     qwen21_edit --update`;
  4. restart the pod, which moves ComfyUI to the new pin and downloads
     about 17.4 GB;
  5. test on a GPU with at least 24 GB, since the template recommends a
     5090 or PRO 6000;
  6. later: the Mentions line, a plan, and Qwen 2.1 LoRAs.
