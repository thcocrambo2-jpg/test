"""Look and feel for the Gradio UI — theme tokens, CSS, header, page JS.

Kept out of ui.py so the UI module stays about controls and handlers.
Nothing here knows what any tab does; ui.py imports THEME/CSS/HEAD/JS and
hands them to `launch()`, which is where Gradio 6 takes them (they are no
longer gr.Blocks arguments).

Everything is a Python string rather than a .css/.js file on purpose:
build.sh compiles the app with Nuitka following imports, so a module comes
along for free while a data file would need its own --include-data-files.

Two rules hold the design together:

  * Colours live in the theme, not in the CSS. Every token below is set
    for light *and* dark (`*_dark` suffix), Gradio flips them on `.dark`,
    and the CSS then refers to them through var(--...) — so one palette
    drives both modes and neither can drift.
  * Class hooks are ours (`kx-` prefix, set via elem_classes in ui.py).
    The CSS reaches into Gradio's own class names only where there is no
    alternative — the tab bar and a few block internals — and those rules
    are grouped together and marked, so a Gradio upgrade has one place to
    check rather than a hunt through the file.
"""

import gradio as gr

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
# Indigo/violet accent over a cool slate neutral: enough colour to mark the
# one action per screen that matters, restrained everywhere else. The 500
# and 600 steps are the only ones that carry brand weight; the rest exist so
# Gradio's derived states (hover, focus, subdued) stay in the same family.
INDIGO = gr.themes.Color(
    c50="#eef1ff", c100="#e0e5ff", c200="#c6cdfe", c300="#a5adfb",
    c400="#858bf6", c500="#6366f1", c600="#4f46e5", c700="#4338ca",
    c800="#3730a3", c900="#2f2a86", c950="#1e1b4b", name="kx_indigo",
)

SLATE = gr.themes.Color(
    c50="#f7f8fa", c100="#eef0f4", c200="#dfe3ea", c300="#c7cdd9",
    c400="#949dae", c500="#6b7385", c600="#4c5364", c700="#363c4b",
    c800="#232833", c900="#151a22", c950="#0a0c11", name="kx_slate",
)

# Named so the CSS below and the header markup can reuse them without
# repeating hex codes. Surfaces are deliberately close together in value —
# an enterprise console separates regions with borders and spacing, not
# with contrast steps that fight the content.
LIGHT_BODY = "#f1f4f8"
LIGHT_SURFACE = "#ffffff"
LIGHT_SUNKEN = "#f7f8fa"
LIGHT_BORDER = "#e2e6ee"

DARK_BODY = "#08090d"
DARK_SURFACE = "#12151c"
DARK_SUNKEN = "#171b23"
DARK_BORDER = "#262b36"

ACCENT = "#6366f1"
ACCENT_ALT = "#8b5cf6"
ACCENT_SOFT_LIGHT = "#eef1ff"
ACCENT_SOFT_DARK = "#1c1f3d"

WARN = "#d97706"
WARN_DARK = "#f59e0b"
OK = "#10b981"


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------
def _theme() -> gr.themes.Base:
    """The Gradio theme. Built once at import; passed to launch()."""
    return gr.themes.Base(
        primary_hue=INDIGO,
        secondary_hue=INDIGO,
        neutral_hue=SLATE,
        text_size=gr.themes.sizes.text_md,
        spacing_size=gr.themes.sizes.spacing_md,
        radius_size=gr.themes.sizes.radius_md,
        # Inter for UI, JetBrains Mono for the paths, seeds and JSON that
        # this app is full of. Both fall through to a system stack, so a
        # pod whose egress blocks fonts.googleapis.com still renders
        # correctly — it just gets the local sans/mono instead.
        font=[gr.themes.GoogleFont("Inter", weights=(400, 500, 600, 700)),
              "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono", weights=(400, 500)),
                   "ui-monospace", "Consolas", "monospace"],
    ).set(
        # -- page + surfaces ------------------------------------------------
        body_background_fill=LIGHT_BODY,
        body_background_fill_dark=DARK_BODY,
        background_fill_primary=LIGHT_SURFACE,
        background_fill_primary_dark=DARK_SURFACE,
        background_fill_secondary=LIGHT_SUNKEN,
        background_fill_secondary_dark=DARK_SUNKEN,
        block_background_fill=LIGHT_SURFACE,
        block_background_fill_dark=DARK_SURFACE,
        panel_background_fill=LIGHT_SUNKEN,
        panel_background_fill_dark=DARK_SUNKEN,
        stat_background_fill=ACCENT,
        stat_background_fill_dark=ACCENT,
        code_background_fill=LIGHT_SUNKEN,
        code_background_fill_dark="#0e1117",

        # -- text -----------------------------------------------------------
        body_text_color="#1b2030",
        body_text_color_dark="#e7eaf1",
        body_text_color_subdued="#5f6779",
        body_text_color_subdued_dark="#98a1b3",
        link_text_color=INDIGO.c600,
        link_text_color_dark=INDIGO.c300,
        link_text_color_hover=INDIGO.c700,
        link_text_color_hover_dark=INDIGO.c200,
        link_text_color_visited=INDIGO.c600,
        link_text_color_visited_dark=INDIGO.c300,
        link_text_color_active=INDIGO.c700,
        link_text_color_active_dark=INDIGO.c200,
        prose_text_weight="400",
        prose_header_text_weight="600",
        section_header_text_size="0.8125rem",
        section_header_text_weight="600",

        # -- borders, radii, elevation --------------------------------------
        border_color_primary=LIGHT_BORDER,
        border_color_primary_dark=DARK_BORDER,
        border_color_accent=INDIGO.c400,
        border_color_accent_dark=INDIGO.c600,
        border_color_accent_subdued=INDIGO.c200,
        border_color_accent_subdued_dark=INDIGO.c800,
        block_border_width="1px",
        block_border_color=LIGHT_BORDER,
        block_border_color_dark=DARK_BORDER,
        block_radius="14px",
        container_radius="12px",
        block_padding="16px",
        block_shadow="none",
        block_shadow_dark="none",
        # One elevation step, used only where something floats above the
        # page (dropdown menus, the sticky action bar).
        shadow_drop="0 1px 2px rgba(15,20,35,.06)",
        shadow_drop_lg="0 12px 32px -8px rgba(15,20,35,.22)",
        layout_gap="14px",
        form_gap_width="1px",

        # -- block labels ("Prompt", "Steps", ...) --------------------------
        block_label_background_fill="transparent",
        block_label_background_fill_dark="transparent",
        block_label_border_width="0px",
        block_label_text_color="#5f6779",
        block_label_text_color_dark="#98a1b3",
        block_label_text_size="0.8125rem",
        block_label_text_weight="500",
        block_label_padding="2px 0",
        block_title_text_color="#3d465c",
        block_title_text_color_dark="#b9c1d1",
        block_title_text_size="0.8125rem",
        block_title_text_weight="500",
        block_info_text_size="0.75rem",
        block_info_text_color="#767e90",
        block_info_text_color_dark="#8b93a5",
        accordion_text_color="#1b2030",
        accordion_text_color_dark="#e7eaf1",

        # -- inputs ---------------------------------------------------------
        input_background_fill=LIGHT_SURFACE,
        input_background_fill_dark="#0f131a",
        input_background_fill_hover=LIGHT_SURFACE,
        input_background_fill_hover_dark="#141922",
        input_background_fill_focus=LIGHT_SURFACE,
        input_background_fill_focus_dark="#141922",
        input_border_color=LIGHT_BORDER,
        input_border_color_dark="#2b313d",
        input_border_color_hover="#cfd5e0",
        input_border_color_hover_dark="#39414f",
        input_border_color_focus=INDIGO.c400,
        input_border_color_focus_dark=INDIGO.c500,
        input_border_width="1px",
        input_radius="10px",
        input_padding="10px 12px",
        input_placeholder_color="#a3aab8",
        input_placeholder_color_dark="#5d6575",
        input_shadow="none",
        input_shadow_focus=f"0 0 0 3px {ACCENT}26",
        input_shadow_focus_dark=f"0 0 0 3px {ACCENT}33",
        input_text_size="0.875rem",

        # -- checkboxes / radios --------------------------------------------
        checkbox_background_color=LIGHT_SURFACE,
        checkbox_background_color_dark="#0f131a",
        checkbox_background_color_selected=INDIGO.c600,
        checkbox_background_color_selected_dark=INDIGO.c500,
        checkbox_background_color_hover=LIGHT_SUNKEN,
        checkbox_background_color_hover_dark="#161b24",
        # A shade darker than the other borders: an empty checkbox is the
        # one control whose whole state is its outline.
        checkbox_border_color="#c9d0dc",
        checkbox_border_color_dark="#3d4553",
        checkbox_border_color_hover="#c2c9d6",
        checkbox_border_color_hover_dark="#454e5f",
        checkbox_border_color_focus=INDIGO.c400,
        checkbox_border_color_focus_dark=INDIGO.c500,
        checkbox_border_color_selected=INDIGO.c600,
        checkbox_border_color_selected_dark=INDIGO.c500,
        checkbox_border_radius="6px",
        checkbox_border_width="1px",
        checkbox_label_background_fill=LIGHT_SUNKEN,
        checkbox_label_background_fill_dark=DARK_SUNKEN,
        checkbox_label_background_fill_hover=LIGHT_SUNKEN,
        checkbox_label_background_fill_hover_dark="#1c222c",
        checkbox_label_background_fill_selected=ACCENT_SOFT_LIGHT,
        checkbox_label_background_fill_selected_dark=ACCENT_SOFT_DARK,
        checkbox_label_border_color=LIGHT_BORDER,
        checkbox_label_border_color_dark=DARK_BORDER,
        checkbox_label_border_color_hover="#cfd5e0",
        checkbox_label_border_color_hover_dark="#39414f",
        checkbox_label_border_color_selected=INDIGO.c300,
        checkbox_label_border_color_selected_dark=INDIGO.c700,
        checkbox_label_border_width="1px",
        checkbox_label_text_color="#3d465c",
        checkbox_label_text_color_dark="#c3cad8",
        checkbox_label_text_color_selected=INDIGO.c700,
        checkbox_label_text_color_selected_dark="#e7eaf1",
        checkbox_label_text_size="0.8125rem",
        checkbox_label_padding="7px 12px",
        checkbox_label_shadow="none",
        checkbox_shadow="none",

        # -- sliders --------------------------------------------------------
        slider_color=INDIGO.c500,
        slider_color_dark=INDIGO.c500,

        # -- buttons --------------------------------------------------------
        # The primary fill is a gradient so the one call to action on a tab
        # reads as the call to action without needing to be huge.
        button_primary_background_fill=(
            f"linear-gradient(135deg, {ACCENT} 0%, {ACCENT_ALT} 100%)"
        ),
        button_primary_background_fill_dark=(
            f"linear-gradient(135deg, {ACCENT} 0%, {ACCENT_ALT} 100%)"
        ),
        button_primary_background_fill_hover=(
            f"linear-gradient(135deg, {INDIGO.c600} 0%, #7c3aed 100%)"
        ),
        button_primary_background_fill_hover_dark=(
            f"linear-gradient(135deg, {INDIGO.c400} 0%, #a78bfa 100%)"
        ),
        button_primary_text_color="#ffffff",
        button_primary_text_color_dark="#ffffff",
        button_primary_text_color_hover="#ffffff",
        button_primary_border_color=INDIGO.c600,
        button_primary_border_color_dark=INDIGO.c600,
        button_primary_shadow=("0 1px 2px rgba(15,20,35,.10), "
                               f"0 6px 18px -8px {ACCENT}80"),
        button_primary_shadow_hover=("0 2px 4px rgba(15,20,35,.12), "
                                     f"0 10px 26px -8px {ACCENT}99"),
        button_primary_shadow_active="0 1px 2px rgba(15,20,35,.14)",
        button_secondary_background_fill=LIGHT_SURFACE,
        button_secondary_background_fill_dark="#1a1f28",
        button_secondary_background_fill_hover=LIGHT_SUNKEN,
        button_secondary_background_fill_hover_dark="#222834",
        button_secondary_text_color="#3d465c",
        button_secondary_text_color_dark="#c3cad8",
        button_secondary_text_color_hover="#1b2030",
        button_secondary_text_color_hover_dark="#ffffff",
        button_secondary_border_color=LIGHT_BORDER,
        button_secondary_border_color_dark="#2f3643",
        button_secondary_border_color_hover="#cfd5e0",
        button_secondary_border_color_hover_dark="#3d4553",
        button_secondary_shadow="none",
        button_secondary_shadow_hover="none",
        button_cancel_background_fill="#dc2626",
        button_cancel_background_fill_dark="#b91c1c",
        button_cancel_background_fill_hover="#b91c1c",
        button_cancel_background_fill_hover_dark="#991b1b",
        button_cancel_text_color="#ffffff",
        button_cancel_border_color="#dc2626",
        button_cancel_border_color_dark="#b91c1c",
        button_border_width="1px",
        button_large_radius="11px",
        button_medium_radius="10px",
        button_small_radius="9px",
        button_large_padding="13px 22px",
        button_medium_padding="9px 16px",
        button_small_padding="6px 12px",
        button_large_text_size="0.9375rem",
        button_large_text_weight="600",
        button_medium_text_weight="500",
        button_small_text_size="0.8125rem",
        button_small_text_weight="500",
        button_transition="all .16s cubic-bezier(.4,0,.2,1)",
        button_transform_hover="translateY(-1px)",
        button_transform_active="translateY(0)",

        # -- feedback -------------------------------------------------------
        error_background_fill="#fef2f2",
        error_background_fill_dark="#2a1416",
        error_border_color="#fca5a5",
        error_border_color_dark="#7f1d1d",
        error_text_color="#b91c1c",
        error_text_color_dark="#fca5a5",
        error_icon_color="#dc2626",
        error_icon_color_dark="#f87171",
        loader_color=ACCENT,
        loader_color_dark=ACCENT_ALT,
        # color_accent has no _dark variant in Gradio 6 — one value serves
        # both modes, which is why the accent was picked to pass contrast on
        # a white and on a near-black surface.
        color_accent=ACCENT,
        color_accent_soft=ACCENT_SOFT_LIGHT,
        color_accent_soft_dark=ACCENT_SOFT_DARK,

        # -- tables ---------------------------------------------------------
        table_border_color=LIGHT_BORDER,
        table_border_color_dark=DARK_BORDER,
        table_even_background_fill=LIGHT_SURFACE,
        table_even_background_fill_dark=DARK_SURFACE,
        table_odd_background_fill=LIGHT_SUNKEN,
        table_odd_background_fill_dark=DARK_SUNKEN,
        table_radius="12px",
    )


THEME = _theme()


# --------------------------------------------------------------------------
# CSS
# --------------------------------------------------------------------------
CSS = f"""
/* ======================================================================
   Krea 2 — application chrome
   Custom hooks are the kx-* classes, applied with elem_classes/elem_id in
   ui.py. Rules that depend on Gradio's own class names are collected in
   the "GRADIO INTERNALS" section at the bottom.
   ====================================================================== */

/* Local tokens. Declared on :root for light and re-declared on .dark —
   Gradio puts that class on <body>, so the body copy wins for every
   descendant and both modes stay in one place. */
:root {{
  --kx-accent: {ACCENT};
  --kx-accent-alt: {ACCENT_ALT};
  --kx-surface: {LIGHT_SURFACE};
  --kx-sunken: {LIGHT_SUNKEN};
  --kx-border: {LIGHT_BORDER};
  --kx-border-soft: #edf0f5;
  --kx-text: #1b2030;
  --kx-muted: #5f6779;
  --kx-faint: #8b93a5;
  --kx-accent-soft: {ACCENT_SOFT_LIGHT};
  --kx-warn: {WARN};
  --kx-warn-soft: #fffbeb;
  --kx-warn-border: #fde68a;
  --kx-err: #dc2626;
  --kx-err-soft: #fef2f2;
  --kx-err-border: #fecaca;
  --kx-ok: {OK};
  --kx-header-bg: rgba(255, 255, 255, .82);
  --kx-glow: radial-gradient(1200px 420px at 12% -8%, {ACCENT}14, transparent 60%),
             radial-gradient(900px 380px at 92% -12%, {ACCENT_ALT}12, transparent 60%);
  --kx-shadow-sm: 0 1px 2px rgba(15, 20, 35, .05);
  --kx-shadow-md: 0 4px 16px -6px rgba(15, 20, 35, .14);
  --kx-shadow-lg: 0 16px 40px -12px rgba(15, 20, 35, .24);
  --kx-scroll-thumb: #ccd2de;
}}

.dark {{
  --kx-surface: {DARK_SURFACE};
  --kx-sunken: {DARK_SUNKEN};
  --kx-border: {DARK_BORDER};
  --kx-border-soft: #1e232c;
  --kx-text: #e7eaf1;
  --kx-muted: #98a1b3;
  --kx-faint: #6f7889;
  --kx-accent-soft: {ACCENT_SOFT_DARK};
  --kx-warn: {WARN_DARK};
  --kx-warn-soft: #1d1608;
  --kx-warn-border: #4d3712;
  --kx-err: #f87171;
  --kx-err-soft: #200e0e;
  --kx-err-border: #55201f;
  --kx-header-bg: rgba(14, 17, 23, .78);
  --kx-glow: radial-gradient(1200px 420px at 12% -8%, {ACCENT}1f, transparent 60%),
             radial-gradient(900px 380px at 92% -12%, {ACCENT_ALT}1a, transparent 60%);
  --kx-shadow-sm: 0 1px 2px rgba(0, 0, 0, .3);
  --kx-shadow-md: 0 4px 16px -6px rgba(0, 0, 0, .5);
  --kx-shadow-lg: 0 16px 40px -12px rgba(0, 0, 0, .6);
  --kx-scroll-thumb: #333b48;
}}

/* ---------------------------------------------------------------- shell */

/* `.app` is Gradio's real content box — it carries the padding and the
   breakpoint-stepped max-width (1536px at this viewport), so widening
   .gradio-container alone would do nothing. These tabs are two columns of
   controls next to a gallery and want the room. */
.gradio-container .app {{
  max-width: 1760px !important;
  padding: 0 clamp(12px, 2vw, 28px) 96px !important;
  /* The glow goes here rather than on <body>: Gradio paints an opaque
     --body-background-fill onto a wrapper above this one at runtime,
     which would cover anything set on the body itself. */
  background-image: var(--kx-glow);
  background-repeat: no-repeat;
}}

/* Gradio sets overflow:hidden here, which makes this element the nearest
   scrollport and stops `position: sticky` further down from ever engaging
   (the page itself scrolls on <body>). `clip` keeps the clipping and drops
   the scrollport, so the header and the action bar can stick. Nothing
   depends on it: without this they simply scroll like any other element. */
.gradio-container {{ overflow: clip !important; }}

/* --------------------------------------------------------------- header */
/* Full-bleed and sticky: the negative side margins cancel the container's
   padding so the bar reaches both edges, and the !important overrides are
   Gradio's own .block chrome (it wraps every component, gr.HTML included,
   in a rounded bordered card). */
#kx-header {{
  position: sticky;
  top: 0;
  z-index: 40;
  /* width:auto is load-bearing: Gradio puts width:100% on every block, so
     the negative margins would slide the bar sideways instead of widening
     it to the full page. */
  width: auto !important;
  margin: 0 calc(-1 * clamp(12px, 2vw, 28px)) 20px !important;
  padding: 13px clamp(12px, 2vw, 28px) !important;
  border-radius: 0 !important;
  border-width: 0 0 1px !important;
  border-bottom: 1px solid var(--kx-border) !important;
  background: var(--kx-header-bg);
  backdrop-filter: saturate(160%) blur(16px);
  -webkit-backdrop-filter: saturate(160%) blur(16px);
}}

/* Shared by the header and the footer. Both are gr.HTML, whose content
   Gradio nests two divs deep, so the flex row has to be our own element
   rather than the block itself. */
.kx-bar {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px 20px;
  flex-wrap: wrap;
}}

#kx-header .kx-brand {{
  display: flex;
  align-items: center;
  gap: 13px;
  min-width: 0;
}}

#kx-header .kx-logo {{
  flex: none;
  width: 40px;
  height: 40px;
  display: grid;
  place-items: center;
  font-size: 20px;
  line-height: 1;
  border-radius: 12px;
  color: #fff;
  background: linear-gradient(135deg, var(--kx-accent), var(--kx-accent-alt));
  box-shadow: 0 6px 18px -6px {ACCENT}b3, inset 0 1px 0 rgba(255, 255, 255, .28);
}}

#kx-header .kx-name {{
  margin: 0;
  font-size: 1.0625rem;
  font-weight: 700;
  letter-spacing: -.012em;
  line-height: 1.25;
  color: var(--kx-text);
}}

#kx-header .kx-tagline {{
  margin: 1px 0 0;
  font-size: .75rem;
  line-height: 1.35;
  color: var(--kx-muted);
}}

#kx-header .kx-stats {{
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}}

/* Engine chips + stat pills share one look; the difference is the accent. */
.kx-pill {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--kx-border);
  border-radius: 999px;
  background: var(--kx-surface);
  color: var(--kx-muted);
  font-size: .75rem;
  font-weight: 500;
  white-space: nowrap;
}}

.kx-pill b {{ color: var(--kx-text); font-weight: 600; }}

.kx-pill-accent {{
  border-color: transparent;
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
}}
.dark .kx-pill-accent {{ color: #b9bcfb; }}

.kx-pill-live {{ gap: 7px; }}

.kx-dot {{
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--kx-ok);
  box-shadow: 0 0 0 3px {OK}26;
  animation: kx-pulse 2.4s ease-in-out infinite;
}}

@keyframes kx-pulse {{
  0%, 100% {{ opacity: 1; }}
  50%      {{ opacity: .45; }}
}}

/* The output path. Clicking copies it — see the JS below. */
.kx-path {{
  font-family: var(--font-mono);
  font-size: .7rem;
  cursor: pointer;
  transition: border-color .15s, color .15s;
}}
.kx-path:hover {{ border-color: var(--kx-accent); color: var(--kx-text); }}
.kx-path::after {{
  content: "⧉";
  font-family: var(--font);
  opacity: .5;
}}
.kx-path.kx-copied {{
  border-color: var(--kx-ok);
  color: var(--kx-ok);
}}
.kx-path.kx-copied::after {{ content: "✓"; opacity: 1; }}

/* ---------------------------------------------------------------- panels */
/* Every generation tab is one control column and one output column, and
   they get opposite treatments on purpose: the controls are a single card
   (they are one form — nesting a card per field is what made the default
   layout read as boxes inside boxes), while the outputs stay as separate
   cards, because a gallery, a status line and a seed really are separate
   results. */
.kx-panel {{
  background: var(--kx-surface);
  border: 1px solid var(--kx-border);
  border-radius: 16px;
  padding: 18px !important;
  box-shadow: var(--kx-shadow-sm);
  position: relative;
  min-width: 0;
  align-self: flex-start;
}}

.kx-panel::before {{
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 2px;
  border-radius: 16px 16px 0 0;
  background: linear-gradient(90deg, var(--kx-accent), var(--kx-accent-alt) 55%, transparent);
  opacity: .75;
}}

/* Everything inside the control card sits directly on it — no inner card
   chrome, spacing alone does the grouping. */
.kx-panel .block {{
  background: transparent !important;
  border-color: transparent !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
}}

.kx-panel-out {{ min-width: 0; }}

.kx-panel-out > .form > .block,
.kx-panel-out > .block {{ box-shadow: var(--kx-shadow-sm) !important; }}

/* Status and "Base seed used" are consecutive form components, so Gradio
   groups them into a .form — and the rule below strips the vertical padding
   off every grouped block. That is right in the control column, where the
   blocks are invisible and the gap does the spacing, but these two are
   cards: with padding-top/bottom at 0 they kept their 16px sides while the
   label sat on the card's top edge and the input on its bottom one. Give the
   output column's grouped blocks a card's padding back on all four sides.
   .gradio-container is only here to outrank that rule, which is otherwise
   the same specificity and comes later in the file. */
.gradio-container .kx-panel-out > .form > .block {{
  padding-top: var(--block-padding) !important;
  padding-bottom: var(--block-padding) !important;
}}

/* Gradio groups consecutive controls into a .form, whose *background* is
   the border colour and whose 1px gap then shows through as hairlines
   between them. That is a lot of lines for a tab with thirty controls, so
   the group loses its frame and a real gap separates the fields instead —
   which also stops two side-by-side sliders' min/max numbers touching. */
.gradio-container .form {{
  background: transparent !important;
  border-color: transparent !important;
  box-shadow: none !important;
  gap: 8px 18px;
}}

.gradio-container .form > .block {{ padding-top: 0 !important; padding-bottom: 0 !important; }}

/* Fine print: the notes Gradio needs inside an accordion, sized so they
   support the controls next to them instead of competing with them. */
.kx-fine p {{
  margin: 2px 0 6px !important;
  font-size: .72rem !important;
  line-height: 1.55;
  color: var(--kx-faint) !important;
}}
.kx-fine strong {{ color: var(--kx-muted); }}

/* --------------------------------------------------------- tab intro note */
.kx-note {{
  border: 1px solid var(--kx-border);
  border-left: 3px solid var(--kx-accent);
  border-radius: 12px;
  background: var(--kx-surface);
  padding: 12px 16px !important;
  margin-bottom: 4px;
}}

.kx-note p:first-child {{ margin-top: 0; }}
.kx-note p:last-child {{ margin-bottom: 0; }}
.kx-note p, .kx-note li {{
  font-size: .8125rem !important;
  line-height: 1.6;
  color: var(--kx-muted);
}}
.kx-note strong {{ color: var(--kx-text); }}
.kx-note code {{ font-size: .75rem; }}

/* ui.py picks the variant from the symbol in the copy: ⚠️ (a model that is
   not downloaded yet) is amber, ❌ (the tab cannot run at all) is red. Only
   the panel is tinted — recolouring the prose as well turned a paragraph
   with six bold phrases into six warnings. */
.kx-note-warn {{
  border-color: var(--kx-warn-border);
  border-left-color: var(--kx-warn);
  background: var(--kx-warn-soft);
}}

.kx-note-error {{
  border-color: var(--kx-err-border);
  border-left-color: var(--kx-err);
  background: var(--kx-err-soft);
}}


/* ------------------------------------------------------- section headings */
/* The "### 🎭 LoRA stack" style markdown headings inside a panel. */
.kx-section h3, .kx-section h4 {{
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 6px 0 2px !important;
  font-size: .6875rem !important;
  font-weight: 700 !important;
  letter-spacing: .085em;
  text-transform: uppercase;
  color: var(--kx-faint) !important;
}}

.kx-section h3::after, .kx-section h4::after {{
  content: "";
  flex: 1;
  height: 1px;
  background: var(--kx-border);
}}

.kx-section p {{
  margin: 6px 0 0 !important;
  font-size: .75rem !important;
  line-height: 1.55;
  color: var(--kx-muted);
}}

/* --------------------------------------------------------------- spec line */
/* The one-liners that sit under a Model dropdown or a size control and say
   what the current selection resolves to. They are rewritten by change
   handlers, so they must read as output, not as a heading. */
.kx-meta p {{
  margin: 0 !important;
  padding: 7px 11px;
  border: 1px solid var(--kx-border-soft);
  border-radius: 9px;
  background: var(--kx-sunken);
  font-size: .74rem !important;
  line-height: 1.5;
  color: var(--kx-muted) !important;
}}

.kx-meta strong {{ color: var(--kx-text); font-weight: 600; }}
.kx-meta code {{ font-size: .7rem; }}

/* ------------------------------------------------------------ status line */
.kx-status textarea {{
  font-family: var(--font-mono) !important;
  font-size: .78rem !important;
  line-height: 1.55;
  background: var(--kx-sunken) !important;
  border-style: dashed !important;
  color: var(--kx-muted) !important;
  resize: none;
}}

/* --------------------------------------------------------- sticky actions */
/* The control columns are long — several run past two screens with the LoRA
   stack open — so the tab's primary button stays pinned to the bottom of
   the viewport while its column is in view instead of scrolling away. */
.kx-cta {{
  position: sticky;
  bottom: 12px;
  /* High on purpose. Gradio's field labels paint through anything under
     ~100 as the button slides past them, and its dropdown menus, modals and
     overlays all sit on --layer-top (2147483647), so they still cover the
     button — which is what you want when a dropdown opens near the bottom
     of the column. */
  z-index: 999;
  width: 100%;
  margin-top: 6px !important;
  letter-spacing: .01em;
  box-shadow:
    0 0 0 7px var(--kx-surface),
    0 8px 22px -10px rgba(15, 20, 35, .45),
    0 6px 18px -8px {ACCENT}80 !important;
}}

@media (max-height: 620px) {{
  .kx-cta {{ position: static; }}
}}

/* ---------------------------------------------------------------- footer */
#kx-footer {{
  margin-top: 22px !important;
  padding: 14px 2px 0 !important;
  border-top: 1px solid var(--kx-border-soft) !important;
  border-radius: 0 !important;
  border-width: 1px 0 0 !important;
  font-size: .72rem;
  color: var(--kx-faint);
}}

#kx-footer .kx-hint {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
}}

/* Gradio's own footer ("Use via API · Built with Gradio · Settings") —
   left in place, just sized down so it reads as chrome. */
.gradio-container footer {{
  font-size: .72rem;
  opacity: .55;
  transition: opacity .16s;
}}
.gradio-container footer:hover {{ opacity: 1; }}

#kx-footer kbd {{
  font-family: var(--font-mono);
  font-size: .66rem;
  padding: 2px 6px;
  border: 1px solid var(--kx-border);
  border-bottom-width: 2px;
  border-radius: 6px;
  background: var(--kx-surface);
  color: var(--kx-muted);
}}

/* ======================================================================
   GRADIO INTERNALS
   Everything below hangs off Gradio's own class names (`.tab-wrapper`,
   `.block`, `.gallery-item`, ...). If a Gradio upgrade changes the UI's
   look, this is the section to re-check — nothing above it depends on
   Gradio's markup.
   ====================================================================== */

/* Tab bar → a segmented control. Gradio measures the buttons at runtime to
   decide what moves into its overflow menu, so only paint is changed here;
   the flex/overflow mechanics it relies on are left alone. */
.tab-wrapper {{
  height: auto !important;
  padding: 5px !important;
  margin-bottom: 18px !important;
  background: var(--kx-surface);
  border: 1px solid var(--kx-border);
  border-radius: 14px;
  box-shadow: var(--kx-shadow-sm);
}}

.tab-container {{ height: 36px !important; gap: 2px; }}
.tab-container::after {{ display: none; }}

.tab-container > button {{
  border-radius: 9px !important;
  padding: 0 13px !important;
  font-size: .8125rem !important;
  font-weight: 500 !important;
  color: var(--kx-muted) !important;
  transition: background-color .15s, color .15s;
}}

.tab-container > button:hover:not(:disabled):not(.selected) {{
  background: var(--kx-sunken) !important;
  color: var(--kx-text) !important;
}}

.tab-container > button.selected {{
  background: var(--kx-accent-soft) !important;
  color: var(--kx-accent) !important;
  font-weight: 600 !important;
}}
.dark .tab-container > button.selected {{ color: #b9bcfb !important; }}

/* Gradio draws a 2px underline under the selected tab; a pill does not
   want one. */
.tab-container > button.selected::after {{ display: none !important; }}

/* Accordions: a quieter header that still reads as a control. */
.gradio-container .label-wrap {{
  padding: 9px 12px !important;
  border-radius: 10px;
  background: var(--kx-sunken);
  font-size: .8125rem;
  font-weight: 500;
  transition: background-color .15s;
}}
.gradio-container .label-wrap:hover {{ background: var(--kx-accent-soft); }}
.gradio-container .label-wrap span {{ font-weight: 500; }}

/* Gallery: rounded thumbnails that lift on hover. */
.gradio-container .thumbnail-item,
.gradio-container .gallery-item {{
  border-radius: 11px !important;
  overflow: hidden;
  transition: transform .16s cubic-bezier(.4, 0, .2, 1), box-shadow .16s;
}}
.gradio-container .gallery-item:hover {{
  transform: translateY(-2px);
  box-shadow: var(--kx-shadow-md);
}}

/* Image / video / editor drop zones read as drop zones. */
.gradio-container .image-container,
.gradio-container .upload-container {{ border-radius: 12px; }}

/* Slider handles: a little larger, and they show the accent on focus. */
.gradio-container input[type="range"] {{ accent-color: var(--kx-accent); }}

/* Keyboard focus is visible everywhere, not just on inputs. */
.gradio-container button:focus-visible,
.gradio-container [role="button"]:focus-visible,
.gradio-container summary:focus-visible {{
  outline: 2px solid var(--kx-accent);
  outline-offset: 2px;
}}

/* Scrollbars, so a tall gallery or JSON box does not draw a grey slab. */
.gradio-container * {{ scrollbar-width: thin; scrollbar-color: var(--kx-scroll-thumb) transparent; }}
.gradio-container ::-webkit-scrollbar {{ width: 10px; height: 10px; }}
.gradio-container ::-webkit-scrollbar-track {{ background: transparent; }}
.gradio-container ::-webkit-scrollbar-thumb {{
  background: var(--kx-scroll-thumb);
  border: 3px solid transparent;
  border-radius: 999px;
  background-clip: content-box;
}}
.gradio-container ::-webkit-scrollbar-thumb:hover {{ background-color: var(--kx-faint); background-clip: content-box; }}

/* Code fences (the JSON batch example) get the mono stack and a border. */
.gradio-container .prose pre {{
  border: 1px solid var(--kx-border);
  border-radius: 10px;
  font-size: .75rem;
}}

/* ------------------------------------------------------------ responsive */
/* Under ~1000px Gradio already stacks rows; make the sticky bits behave and
   let the header wrap instead of squeezing. */
@media (max-width: 1000px) {{
  #kx-header {{ position: static; }}
  #kx-header .kx-bar {{ align-items: flex-start; }}
  .kx-panel {{ padding: 14px !important; }}
}}

@media (max-width: 860px) {{
  .gradio-container .row {{ flex-wrap: wrap; }}
  .kx-panel, .kx-panel-out {{ flex: 1 1 100% !important; min-width: 100% !important; }}
}}

@media (prefers-reduced-motion: reduce) {{
  .gradio-container *,
  .kx-dot {{
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }}
  .gradio-container .gallery-item:hover {{ transform: none; }}
}}
"""


# --------------------------------------------------------------------------
# <head> — favicon, so a pinned tab is identifiable
# --------------------------------------------------------------------------
# Inline SVG data URI rather than launch(favicon_path=...): a path would be
# one more data file for the Nuitka build to carry.
_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop offset='0' stop-color='%236366f1'/%3E"
    "%3Cstop offset='1' stop-color='%238b5cf6'/%3E%3C/linearGradient%3E%3C/defs%3E"
    "%3Crect width='100' height='100' rx='24' fill='url(%23g)'/%3E"
    "%3Cpath d='M56 12 26 58h20l-6 30 32-48H50z' fill='white'/%3E%3C/svg%3E"
)

HEAD = f"""
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<meta name="color-scheme" content="light dark">
<meta name="description" content="Krea 2 — ComfyUI generation suite on RunPod">
"""


# --------------------------------------------------------------------------
# Page JS
# --------------------------------------------------------------------------
# Two conveniences, both additive: nothing here is required for any control
# to work, and both are written to no-op rather than throw if Gradio's DOM
# is not what they expect.
JS = """
() => {
  // Ctrl/Cmd+Enter runs the tab you are looking at. Resolved at press time
  // from the visible tab panel, so it follows the user across tabs and
  // never fires for a tab that is not on screen.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
    const panels = document.querySelectorAll(".tabitem");
    for (const panel of panels) {
      if (!panel.offsetParent) continue;             // hidden tab
      const button = panel.querySelector("button.primary:not([disabled])");
      if (button) {
        event.preventDefault();
        button.click();
      }
      return;
    }
  });

  // Click the output-path pill to copy it. The pod's disk is ephemeral, so
  // this path gets typed into scp/rsync often enough to be worth a click.
  document.addEventListener("click", async (event) => {
    const pill = event.target.closest(".kx-path");
    if (!pill) return;
    const text = pill.dataset.kxCopy || pill.textContent;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.appendChild(scratch);
      scratch.select();
      document.execCommand("copy");
      scratch.remove();
    }
    pill.classList.add("kx-copied");
    setTimeout(() => pill.classList.remove("kx-copied"), 1400);
  });
}
"""


# --------------------------------------------------------------------------
# Header / footer markup
# --------------------------------------------------------------------------
def _escape(text) -> str:
    """Minimal HTML escape — these strings are paths and counts, not prose."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def header_html(engines, model_count: int, gpu_count: int, output_dir) -> str:
    """The application bar.

    `engines` is the list of engine names this license turned on, in tab
    order; it replaces the old "Krea 2 + Flux 2 + ..." title, which grew a
    term every time a feature shipped. The counts and the output path are
    the same facts the previous header's second line carried.
    """
    chips = "".join(
        f'<span class="kx-pill kx-pill-accent">{_escape(name)}</span>'
        for name in engines
    )
    path = _escape(output_dir)
    return f"""
<div class="kx-bar">
  <div class="kx-brand">
    <div class="kx-logo">⚡</div>
    <div>
      <h1 class="kx-name">Krea 2</h1>
      <p class="kx-tagline">ComfyUI generation suite · RunPod
        · native multi-GPU placement</p>
    </div>
  </div>
  <div class="kx-stats">
    {chips}
    <span class="kx-pill kx-pill-live"><i class="kx-dot"></i>Live</span>
    <span class="kx-pill"><b>{model_count}</b> model{'' if model_count == 1 else 's'}</span>
    <span class="kx-pill"><b>{gpu_count}</b> GPU{'' if gpu_count == 1 else 's'}</span>
    <span class="kx-pill kx-path" data-kx-copy="{path}"
          title="Click to copy the output directory">{path}</span>
  </div>
</div>
"""


FOOTER_HTML = """
<div class="kx-bar">
  <div class="kx-hint">
    <kbd>Ctrl</kbd><span>+</span><kbd>Enter</kbd>
    <span>runs the tab you are on</span>
  </div>
  <div>Krea 2 · ComfyUI on RunPod</div>
</div>
"""


def launch_kwargs() -> dict:
    """The look-and-feel arguments for `Blocks.launch()`.

    One place for them because two callers launch the same UI: ui.launch_ui()
    on a pod and scripts/dryrun.py locally. Anything that only affects how
    the app *looks* belongs here; ports, sharing and allowed paths stay with
    the caller, which is what actually differs between the two.
    """
    return {"theme": THEME, "css": CSS, "head": HEAD, "js": JS}
