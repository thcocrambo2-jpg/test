# Design a static HTML mockup: the Qwen 2.1 Reference tab

You are designing, not implementing. Produce **one self-contained static
HTML file** that shows what a new Ember tab, **🧩 Qwen 2.1 Reference**,
should look like. It must look like it already belongs in the app. The user
will review it, pick between the options it shows, and only then have the
real feature built.

**Don't change any code.** Leave `ember/`, `webui/`, `license-validator/`,
`scripts/` and `docs/` untouched, and don't commit anything. Your only
output is the mockup file (§6).

Read `prompts/64-qwen21-ref/RESEARCH.md` first: it says what the workflow
does and which controls exist. What follows is the design brief.

---

## 1. The feature in one paragraph

The user writes a prompt and uploads **1 to 10 reference images** (a
character, an outfit, a product, a location). Qwen Image 2.1 then makes one
new image that keeps those references consistent. The references are
**numbered**, and the number matters in two ways:

- **Reference 1 is special.** It can set the output's shape (with the
  "Same as reference 1" size option), and the model treats it as the main
  subject.
- **Prompts refer to images by number**, as in "the woman from image 1
  wearing the jacket from image 3". Numbers must be visible and stable.

## 2. Match the existing app. Look before you draw.

The mockup has to sit visually next to the real tabs. Study them first.

1. **Run the app without a GPU** and look at it:
   `$PY scripts/dryrun.py --features all --port 7870`, using the `krea2`
   conda env (`%USERPROFILE%\miniconda3\envs\krea2\python.exe`).
   - **Never use port 7860**; the user's real app runs there.
   - Stop the dry run when you're done.
   - Puppeteer-core with the installed Chrome can take screenshots. Tick
     the 18+ box on the terms screen first.
   - Screenshot at least **✨ Krea2 Edit** (the closest existing tab: it
     has image inputs), **🎨 Krea2**, **🔶 Krea2 V2** and **⚡ Z-Image**,
     in dark mode and light mode, at desktop width and at phone width.
     Keep the screenshots in your scratchpad; they're reference only.
2. **Read the styling source.** Copy the look; don't invent a new one.
   - `webui/src/theme/tokens.css` is the whole palette, light and dark.
     Dark is the default. Copy the tokens you need **verbatim** into the
     mockup's `<style>`, including the `[data-theme="dark"]` overrides.
   - `webui/src/theme/base.css`: type, spacing and base elements.
   - `webui/src/components/fields/` (`index.tsx` + `fields.module.css`):
     the existing single-image drop zone `ImageDropField` (click / drop /
     Ctrl+V, the preview with a "W × H" chip, Remove/Replace), and the
     `RecentStrip` of recent outputs under every image input.
   - `webui/src/components/` `TwoColumn`, `SchemaForm`, `LoraStack`,
     `SeedRow`, `SamplerPanel`, `PresetBar`, `ui/`: the columns, sections,
     buttons, sliders, selects and toggles.
   - `webui/src/features/tabs/` `GenerateTab.tsx`, `OutputPanel.tsx`,
     `tabs.module.css`: the tab shell and the result panel.
   - `webui/src/features/shell/`: the header and grouped navigation
     (Generate / Edit / Video / Library). The new tab sits under **Edit**.
   - `docs/architecture/web-ui.md`: layout rules. Note that no component
     carries a colour literal; do the same (tokens only).
3. **Reuse, don't reinvent.** Every section that already exists (Prompt,
   Output, Sampler, Seed & batch, Presets, LoRA stack, the result panel,
   the Generate button, the queue/status line) should look **identical** to
   how Krea2 Edit renders it. Only the reference-images field is new.

## 3. The tab's contents

Put them in the same places the Edit tabs use. Check where Krea2 Edit puts
its images: its `Images` group is in the right-hand column, next to the
result (`G_INPUTS`, `column="right"`).

| Section | Controls (defaults) |
| --- | --- |
| **References**, the new part | 1–10 images; see §4 |
| Prompt | Prompt (textarea, with the existing undo / redo / clear buttons); Negative prompt (collapsed, with the existing "Above 1 turns the negative prompt on" hint) |
| Output | Output size select: `Same as reference 1`, `1:1 · 2048×2048` (workflow default), `4:3 · 2400×1792`, `3:4 · 1792×2400`, `3:2 · 2528×1696`, `2:3 · 1696×2528`, `16:9 · 2752×1536`, `9:16 · 1536×2752`; Model dropdown ("Qwen Image 2.1 int8", with the model-info line underneath, as other tabs have) |
| Sampler (collapsible) | Steps 25 (1–60), CFG 1.0, Sampler `euler`, Scheduler `simple` (other choice `beta`) |
| Advanced (collapsible, closed) | Reference detail: 1024 (0–4096, step 32), hint "How finely each reference is read. 0 keeps each image's own size." |
| Seed & batch | the existing seed row + batch count |
| Presets | the existing save-preset controls |
| LoRA stack | the existing stack, at the bottom, the model + CLIP variant (as on Krea2 V2) |
| Result panel | the existing output panel, with a finished image in it |

## 4. The new reference-images field: the actual design work

It replaces the template's in-ComfyUI "Qwen Image References Manager" (a
5×2 thumbnail grid). Design it as one field that holds up to 10 images:

- **Small numbered thumbnails.** Square or cover-cropped tiles in a grid.
  Each has a clear **number badge (1–10)** in the corner. Reference 1 is
  marked as the main reference (for example a "Main" chip or an accent
  outline).
- **An "Add" tile** at the end of the grid: click to choose (multi-select
  allowed), drop several files at once, or Ctrl+V to paste. It shows the
  count, like "3 / 10". It disappears or disables at 10.
- **Per tile**, on hover or focus:
  - remove (×);
  - a drag handle to **reorder**, because reordering renumbers;
  - the image size ("1920 × 1080") as a small chip, like the existing
    preview chip.
  - Also show, as a **proposal the user can reject**: rotate ↻, mirror ⇋
    and crop ✂ buttons, which the template's node offered. Label them
    clearly in the annotations as optional.
- **A larger preview** of the selected or hovered tile, so faces can be
  checked. Decide whether it's inline or a lightbox; the app has a
  lightbox in `webui/src/features/gallery/Lightbox.tsx`.
- **The recent-outputs strip** (`RecentStrip`) under the field, as on every
  image input, so a picture made earlier can be dropped in as a reference.
- **A hint line** such as "Refer to images by number in your prompt: 'the
  woman in image 1 wearing the jacket from image 2'."
- **Validation states:** no references yet ("Add at least one reference");
  an 11th image refused ("Up to 10 references"); a non-image file refused.

**Show these states**, each as its own labelled static panel on the page or
behind a small state switcher at the top:

1. Empty: no references yet; Generate disabled or showing the error hint.
2. Three references, with reference 1 marked main and one tile in its hover
   state showing the tile controls.
3. Ten references, full: the Add tile gone or disabled, and the grid still
   tidy.
4. Dragging files over the field (drop-active highlight).
5. Reordering: one tile mid-drag, with the numbers about to shift.
6. The Output size select open on "Same as reference 1", with a note that
   the output will be 1536 × 2048 (reference 1's shape).

**Two layout variants**, side by side or switchable, so the user can
choose:

- **A.** References in the **right** column above the result, like Krea2
  Edit's images.
- **B.** References in the **left** column at the top, above Prompt, with
  the right column left to the result alone.

Say in an annotation which one you'd pick at 1440 px and at phone width,
and why.

## 5. Rules for the file

- **Static.** No real uploads, no fetches, no app logic. The only
  JavaScript allowed is purely presentational: a light/dark toggle, a
  state switcher and a layout A/B switcher. Everything else is markup and
  CSS.
- **Self-contained.** One `.html` file, inline CSS. Placeholder images are
  inline SVG or CSS gradients (portrait-, outfit- and product-looking
  shapes are fine), **not** real photos and not remote URLs, so the file
  opens offline. Fonts: the app's own stack; Inter from Google Fonts is
  fine if the app uses it.
- **Theme.** Default dark with a working toggle to light, both taken from
  the copied tokens. No colour literals outside the token block.
- **Responsive.** Check it at 1440 px, 1024 px and 390 px. No horizontal
  page scroll at phone width. The thumbnail grid reflows (5 columns → 4 →
  3).
- **Accessible.** Real buttons with labels, visible focus rings (the app's
  `--c-focus`), and the number badges must not rely on colour alone.
- **Annotations.** Small, clearly separate callouts (toggleable, for
  example with an "Annotations" switch) that explain each new decision:
  why numbers, what "Main" means, what's optional (rotate / mirror /
  crop), and the two layout variants. Existing, reused sections need no
  annotations beyond "same as Krea2 Edit".
- The mock data should look real: realistic image sizes, a sensible sample
  prompt using image numbers, and a seed.

## 6. Output

- Write the mockup to **`prompts/64-qwen21-ref/mockup/qwen21-reference.html`**.
- Take screenshots of your own mockup (dark, light, phone width, each
  state) and compare them with the real Krea2 Edit screenshots. Fix
  anything that doesn't match (spacing, radius, font sizes, colours,
  section headers) before you finish.
- Final message: the file path; a short list of the design decisions the
  user has to make (layout A or B; keep rotate/mirror/crop or not;
  inline preview or lightbox; default output size, "Same as reference 1"
  or 2048²); and anything in the brief that turned out to conflict with
  how the app really looks.
