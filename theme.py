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

import re
from collections import namedtuple
from datetime import datetime, timezone

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
   Ember — application chrome
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
  /* Pricing figures. Deep amber on white and soft gold on near-black —
     two values because one warm tone cannot clear 4.5:1 on both. */
  --kx-price: #9a6206;
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
  --kx-price: #f0c56a;
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
   in a rounded bordered card).
   The bar is a gr.Row, not a single gr.HTML, because the Plans & pricing
   button in it has to be a real Gradio control — it opens a view of this
   same page, which no link can do. The rules below put the markup block and
   that button back on one line. */
#kx-header {{
  position: sticky;
  top: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  gap: 8px 16px;
  flex-wrap: nowrap;
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

/* The header's static half (brand + licence pills) takes the room; the
   button keeps its own width instead of being stretched by the Row. */
#kx-header > .kx-headline {{ flex: 1 1 auto; min-width: 0; }}
#kx-header .kx-navbtn {{ flex: none !important; width: auto !important; }}

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

/* The mark inside the tile. Height rather than width, because the flame is
   much taller than it is wide and it is the height that has to sit inside
   the tile's padding; `fill: currentColor` picks up the white above. */
#kx-header .kx-logo svg {{
  display: block;
  width: auto;
  height: 22px;
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

/* The right-hand pills — the licence in the header, the counts and the
   output path in the footer. */
.kx-stats {{
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}}

/* The plan chip, the stat pills and the path pill share one look; the
   difference is the accent (current plan) or the amber (expiring soon). */
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

/* An expiry inside the last week. Amber and not red on purpose: the key
   still works, and it is the customer's supplier who renews it, so this is
   a heads-up and not a failure. */
.kx-pill-warn {{
  border-color: var(--kx-warn-border);
  background: var(--kx-warn-soft);
  color: var(--kx-warn);
}}

/* The row holding the pricing panel's own buttons — Back and Refresh.
   Right-aligned and out of the way: it is chrome, not a control any
   generation flow needs. Gradio stretches a Row's children, so the buttons
   are pinned to their own width here rather than spanning the page. */
.kx-navrow {{
  justify-content: flex-end !important;
  gap: 8px;
  margin-bottom: 10px !important;
}}
.kx-navrow > * {{ flex: none !important; width: auto !important; }}

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

/* The Prompt Library's filter row, for exactly the reason .kx-panel-out
   needs the same rule above: the tab has no control column, so these
   blocks keep their default card chrome — a border and a background — and
   the rule above then leaves the "Tab" / "Source" / "Search" label sitting
   on the card's top edge and the control on its bottom one. Give all four
   sides a card's padding back.

   Descendant selectors rather than child ones because Gradio nests a
   .form under the row only when it groups consecutive inputs: the two
   radios are grouped, the search box next to a button is not, and both
   have to land. */
.gradio-container .kx-lib-filters .block {{
  padding-top: var(--block-padding) !important;
  padding-bottom: var(--block-padding) !important;
}}

/* The radio pills wrap onto a second line on a narrow screen; without this
   they sit flush against the label above them. */
.kx-lib-filters .wrap {{ gap: 6px; }}

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

/* Gradio hangs `elem_classes` on *both* the block wrapper and the markdown
   div inside it, so the note above is drawn twice — a bordered card sitting
   inside a bordered card. The wrapper is the one with the layout box, so the
   inner copy gives its chrome back. */
.kx-note[data-testid="markdown"] {{
  border: 0 !important;
  border-radius: 0;
  background: none !important;
  padding: 0 !important;
  margin: 0;
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

/* -------------------------------------------------------------- negative */
/* The folded negative prompt. Quiet chrome: it sits between the prompt and
   the model picker, and an accordion styled like the panels around it would
   read as a section of the form rather than as one line that can be opened
   when it is wanted. */
.kx-negative {{
  border: none !important;
  background: transparent !important;
  margin: -2px 0 2px !important;
}}
/* The right padding is not decoration and must not go to zero. Gradio turns
   the heading's ▼ by 90° to point it sideways when the accordion is shut, and
   a rotated box is measured by its *bounding* box: an 11px-wide arrow that is
   18px tall becomes 18px wide, so it hangs ~3.5px past the block. Gradio gives
   .block overflow:auto, and that overhang is enough to raise a horizontal
   scrollbar under the heading. Its own 12px absorbs it; ours has to keep
   enough to do the same. In em so it holds if the font is scaled. */
.kx-negative > button,
.kx-negative > .label-wrap {{
  padding: 2px .45em 2px 0 !important;
  font-size: .74rem !important;
  color: var(--kx-muted) !important;
}}
.kx-negative > button:hover,
.kx-negative > .label-wrap:hover {{ color: var(--kx-text) !important; }}

/* ------------------------------------------------------------------ undo */
/* The undo/redo pair the page JS puts under every prompt box. Small and
   quiet — they are a way back, not an action anyone is looking for — and
   right-aligned under the box so they read as belonging to it rather than
   to whatever control comes next. */
.kx-undo {{
  display: flex;
  justify-content: flex-end;
  gap: 5px;
  margin-top: 5px;
}}
.kx-undo-btn {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 27px;
  padding: 0;
  border: 1px solid var(--kx-border);
  border-radius: 7px;
  background: var(--kx-surface);
  color: var(--kx-muted);
  cursor: pointer;
  transition: color .15s, border-color .15s, background .15s;
}}
.kx-undo-btn svg {{
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}}
.kx-undo-btn:hover:not(:disabled) {{
  color: var(--kx-text);
  border-color: var(--kx-accent);
}}
.kx-undo-btn:active:not(:disabled) {{ background: var(--kx-sunken); }}
.kx-undo-btn:disabled {{ opacity: .3; cursor: default; }}

/* A thumb, not a mouse pointer — which is the case these buttons exist
   for, so they get a real tap target rather than a scaled-down one. */
@media (pointer: coarse) {{
  .kx-undo-btn {{ width: 42px; height: 34px; }}
  .kx-undo-btn svg {{ width: 17px; height: 17px; }}
}}

/* ------------------------------------------------------- the gallery tab */
/* The Gallery tab is a viewer, not a form: a stage with one picture on
   it, a slim bar under that, and a filmstrip of small tiles under that.
   One column all the way down, so the phone layout is this one with
   smaller numbers rather than a second arrangement to keep working.

   The stage is a fixed height on purpose. Sizing it to each picture
   would move the filmstrip up and down the page on every step, and the
   strip is the thing being aimed at. That height lives *here* rather
   than on the components, which is what centres them: given a height of
   its own, Gradio's image block letterboxes at the top of it and leaves
   the whole difference as a gap underneath. */
.kx-stage {{
  --kx-stage-h: min(58vh, 560px);
  position: relative;
  /* !important because this one declaration is the whole fix: it is what
     the picture is centred in and sized against. */
  height: var(--kx-stage-h) !important;
  border: 1px solid var(--kx-border);
  border-radius: 14px;
  background: var(--kx-sunken);
  overflow: hidden;
  padding: 0 !important;
  gap: 0 !important;
  /* Both axes, for whichever of the two blocks is on show — and for
     neither of them, which is what an empty gallery looks like. */
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}}

/* The picture sits *on* the stage: its own card chrome would be a box
   drawn inside a box. Sized, but never given a `display` — the hidden
   one of the pair is hidden by exactly that property. */
.kx-stage .block {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
  width: 100% !important;
  height: 100% !important;
  min-height: 0 !important;
}}

/* Everything between the block and the picture is made a centring box of
   the stage's size, so the picture is centred by whichever of these
   Gradio actually renders. A name that is not in the DOM matches
   nothing and costs nothing. */
.kx-stage .image-container,
.kx-stage .image-frame,
.kx-stage .video-container,
.kx-stage .wrap,
/* And the same again by what a box *contains* rather than by what it is
   called, which catches any of them this version of Gradio renders
   under a name not in the list above. Descendants of the block only:
   the block itself is what `visible=False` hides, and hiding is a
   `display`, so a rule that set one here would put a hidden picture
   back on the stage. */
.kx-stage .block :has(img),
.kx-stage .block :has(video) {{
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  width: 100% !important;
  height: 100% !important;
  max-height: none !important;
}}

/* And the picture itself is only ever as big as it can be without being
   cropped or overflowing. The height is spelled out as well as given as
   a percentage: a percentage resolves to nothing if any box between
   here and the stage turns out to size itself to its content. */
.kx-stage img,
.kx-stage video {{
  width: auto !important;
  height: auto !important;
  max-width: 100% !important;
  max-height: calc(var(--kx-stage-h) - 2px) !important;
  object-fit: contain !important;
  margin: auto !important;
}}

/* Fullscreen. The browser hands the element the whole screen but keeps
   its own styling, so without this the picture stays the size the stage
   gave it, on a transparent panel, with the page showing through
   behind — which is what it did. */
.kx-stage :fullscreen,
.kx-stage:fullscreen {{
  width: 100vw !important;
  height: 100vh !important;
  max-width: none !important;
  max-height: none !important;
  border-radius: 0 !important;
  background: var(--kx-sunken) !important;
}}
.kx-stage :fullscreen img,
.kx-stage :fullscreen video,
.kx-stage:fullscreen img,
.kx-stage:fullscreen video {{
  max-width: 100vw !important;
  max-height: 100vh !important;
}}
.kx-stage :fullscreen::backdrop,
.kx-stage:fullscreen::backdrop {{ background: var(--kx-sunken); }}

/* Gradio's own download and fullscreen buttons live in the picture's top
   right corner, and the click zones below would otherwise be laid over
   them. Lift them out — and because that depends on Gradio's class
   names, also keep the zones off the top of the stage entirely, so the
   buttons stay reachable even if a version renames their wrapper. */
.kx-stage .icon-button-wrapper,
.kx-stage .icon-buttons,
.kx-stage .top-panel {{ z-index: 5 !important; }}

/* Click the left or right of the picture to walk the list — where the
   pointer already is, rather than a Prev/Next pair somewhere above it.
   They are real buttons on the same handler as the arrow keys, so an end
   of the list disables one and stops it taking clicks.

   Nothing is drawn for them. A pair of chevrons floating in the margins
   of a picture is a permanent piece of furniture in aid of a gesture
   that is discovered once and then known; the pointer turning into a
   hand over the picture is the whole hint that is wanted. */
.kx-zones {{
  position: absolute !important;
  inset: 42px 0 0 0;
  display: flex !important;
  flex-wrap: nowrap !important;
  gap: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  pointer-events: none;
  z-index: 3;
}}
.kx-zones > * {{
  flex: 1 1 50% !important;
  min-width: 0 !important;
  display: flex !important;
}}
.kx-zones button {{
  flex: 1 1 auto !important;
  width: 100% !important;
  min-width: 0 !important;
  height: 100% !important;
  padding: 0 !important;
  border: 0 !important;
  border-radius: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  pointer-events: auto;
  cursor: pointer;
  /* The label stays for a screen reader, which is the only thing that
     reads these at all. */
  font-size: 0 !important;
  color: transparent !important;
}}
.kx-zones button > * {{ font-size: 0 !important; }}
/* A button Gradio would otherwise light up on hover and press down on
   click, laid over half a picture. */
.kx-zones button:hover,
.kx-zones button:active {{
  background: transparent !important;
  box-shadow: none !important;
  transform: none !important;
}}
.kx-zones button:disabled {{ cursor: default; pointer-events: none; }}
/* Keyboard focus has to land somewhere visible, and a transparent
   button that has taken focus is otherwise a lost cursor. */
.kx-zones button:focus-visible {{
  outline: 2px solid var(--kx-accent) !important;
  outline-offset: -4px;
}}

/* The bar under the picture: where you are on the left, the bin on the
   right, and the confirm step in between rather than in a row of its own
   — arming a delete should not shove the filmstrip down the page. */
.kx-gal-bar {{
  align-items: center !important;
  flex-wrap: wrap;
  gap: 8px !important;
  margin: 10px 0 !important;
}}
.kx-gal-bar > * {{
  flex: none !important;
  width: auto !important;
  /* The confirm step is a .kx-navrow, which carries a bottom margin for
     the pricing panel it was written for. In a row that centres its
     children that margin is a shove upwards. */
  margin-bottom: 0 !important;
}}
.kx-gal-bar > .kx-gal-pos {{ flex: 1 1 auto !important; min-width: 0; }}
.kx-gal-pos p {{
  margin: 0 !important;
  /* The count changes on every step and sits next to nothing else that
     moves — proportional digits would make it twitch. */
  font-variant-numeric: tabular-nums;
}}
.kx-gal-pos strong {{ font-size: .8125rem; }}

/* A bin, not a labelled button: it acts on the picture directly above
   it, which is the only thing it could act on. */
.kx-icon-btn, .kx-icon-btn button, button.kx-icon-btn {{
  flex: none !important;
  width: 38px !important;
  min-width: 38px !important;
  padding: 0 !important;
  font-size: 15px !important;
}}

/* The filmstrip. Gradio lays a Gallery out as a wrapping grid of its own
   making; this has to be one row that scrolls sideways, which is what
   keeps it a way *to* the picture rather than a second screenful of
   pictures competing with the one above it.

   **The block itself is the scroller**, not anything inside it. Which
   element holds the tiles is Gradio's business and has moved between
   versions — so the JS in this file finds the tiles' actual parent, lays
   that out as a nowrap row of max-content width, and opens up every box
   between it and here. The rules below are the same thing said in CSS
   for the class names this version happens to use: they make the strip
   right before the JS runs, and harmless if it never does. */
.kx-strip {{
  overflow-x: auto !important;
  overflow-y: hidden !important;
  height: auto !important;
  min-height: 0 !important;
}}
.kx-strip .grid-wrap,
.kx-strip .grid-container {{
  display: flex !important;
  flex-wrap: nowrap !important;
  grid-template-columns: none !important;
  gap: 8px !important;
  width: max-content !important;
  min-width: 100% !important;
  max-width: none !important;
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  overflow: visible !important;
  padding: 2px !important;
}}
.kx-strip .grid-container > *,
.kx-strip .thumbnail-item,
.kx-strip .gallery-item {{
  flex: 0 0 auto !important;
  width: var(--kx-tile, 78px) !important;
  height: var(--kx-tile, 78px) !important;
  /* A tile is a whole picture shrunk to fit, not a square cut out of the
     middle of one: the strip is how you tell two generations of the same
     prompt apart, and cropping takes away the half that differs. */
  background: var(--kx-sunken);
}}
.kx-strip .thumbnail-item img,
.kx-strip .gallery-item img {{
  width: 100% !important;
  height: 100% !important;
  object-fit: contain !important;
}}
/* The tile being shown is the only one at full strength: the strip is
   read out of the corner of the eye while the picture has the middle of
   it. */
.kx-strip .thumbnail-item,
.kx-strip .gallery-item {{ opacity: .7; transition: opacity .15s; }}
.kx-strip .thumbnail-item:hover,
.kx-strip .gallery-item:hover {{ opacity: 1; }}
.kx-strip .selected,
.kx-strip [aria-selected="true"] {{
  opacity: 1 !important;
  outline: 2px solid var(--kx-accent) !important;
  outline-offset: -2px;
}}

/* The count of what is loaded, and the button that loads more of it. */
.kx-strip-bar {{
  align-items: center !important;
  gap: 8px !important;
  margin-top: 6px !important;
}}
.kx-strip-bar > * {{ flex: none !important; width: auto !important; }}
.kx-strip-bar > *:first-child {{ flex: 1 1 auto !important; min-width: 0; }}

/* The ← → keys' buttons. Off screen but *rendered*: the JS below refuses
   to fire while they are not on screen, which is what stops the arrow
   keys doing anything on another tab, and `visible=False` would take
   that test away along with them. Clipped rather than display: none for
   the same reason — and being real, focusable buttons they are also the
   keyboard way through the list, which the click zones over the picture
   are not. */
.kx-sr-nav {{
  position: absolute !important;
  top: 0;
  left: 0;
  width: 1px !important;
  height: 1px !important;
  min-width: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
  clip-path: inset(50%);
  white-space: nowrap;
}}

/* The Gallery tab's Delete button. Quiet until it is pointed at: the
   control is permanent and unrecoverable, so it should be findable
   without being the loudest thing next to a picture someone is enjoying.
   The confirm button beside it is Gradio's own `variant="stop"`, which is
   red from the start — by then the warning is the point. */
.kx-danger button, button.kx-danger {{
  color: var(--kx-err) !important;
  border-color: var(--kx-err-border) !important;
}}
.kx-danger button:hover, button.kx-danger:hover {{
  background: var(--kx-err-soft) !important;
  border-color: var(--kx-err) !important;
}}

/* ---------------------------------------------------------------- recipe */
/* The Gallery tab's "how this was made" panel. Its body is a settings
   table plus the prompt as a quote, so it is read rather than scanned —
   the table stays narrow and the quote gets room to breathe. */
.kx-recipe table {{
  width: auto;
  font-size: .74rem;
  border-collapse: collapse;
  margin: 6px 0 2px;
}}
.kx-recipe th, .kx-recipe td {{
  padding: 3px 12px 3px 0 !important;
  border: none !important;
  border-bottom: 1px solid var(--kx-border) !important;
  text-align: left;
}}
.kx-recipe th {{ color: var(--kx-muted); font-weight: 600; }}
.kx-recipe td code {{ font-size: .72rem; }}
.kx-recipe blockquote {{
  margin: 4px 0 10px !important;
  padding: 6px 12px !important;
  border-left: 2px solid var(--kx-accent) !important;
  background: var(--kx-sunken);
  font-size: .78rem;
  line-height: 1.5;
}}
.kx-recipe blockquote p {{ margin: 0 !important; }}

/* ----------------------------------------------------------------- queue */
/* The job queue sits under the tabs, so it is chrome on every screen and
   has to read as a list rather than as another panel of controls. Rows are
   flat and separated by a rule; the row body is the only thing that grows,
   which keeps every ✕ in the same column no matter how long a prompt is. */
.kx-queue {{ margin-top: 10px; }}
.kx-queue .kx-queue-row {{
  align-items: center;
  gap: 10px;
  padding: 8px 2px;
  border-top: 1px solid var(--kx-border);
  margin: 0 !important;
}}
.kx-queue .kx-queue-row:first-of-type {{ border-top: none; }}
.kx-queue .kx-queue-body {{ min-width: 0; flex: 1 1 auto !important; }}
/* Three lines of markdown per row — what it is, its prompt, how it is
   going — tightened up so a full queue is still one screen. */
.kx-queue .kx-queue-body p {{
  margin: 0 0 2px !important;
  font-size: .78rem;
  line-height: 1.45;
  overflow-wrap: anywhere;
}}
.kx-queue .kx-queue-body p:last-child {{ margin-bottom: 0 !important; }}
.kx-queue .kx-queue-body small {{
  font-family: var(--font-mono);
  font-size: .68rem;
  color: var(--kx-muted);
}}
.kx-queue .kx-queue-row button {{ flex: none !important; }}

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

/* --------------------------------------------------------------- pricing */
/* The /pricing route. Read-only, no controls at all, so it is one HTML
   block (see pricing_html) laid out with a real grid instead of a nest of
   Gradio rows. auto-fit + minmax lets the column count follow the width, so
   four tiers on a desktop and one on a phone need no breakpoint here. */
.kx-pricing {{ max-width: 1180px; margin: 0 auto; }}

.kx-pricing-head {{ margin-bottom: 20px; }}

.kx-pricing-head h2 {{
  margin: 0 0 6px !important;
  font-size: 1.35rem !important;
  font-weight: 700 !important;
  letter-spacing: -.01em;
  color: var(--kx-text) !important;
}}

.kx-pricing-head p {{
  margin: 0 !important;
  max-width: 68ch;
  font-size: .85rem !important;
  line-height: 1.65;
  color: var(--kx-muted) !important;
}}

/* ---------------------------------------------------- billing cycle tabs */
/* Monthly / Quarterly / Yearly, as a segmented control over the card grid.
   The whole thing is CSS — hidden radio inputs sitting before the bar, and
   sibling selectors that light one tab and reveal one price block per card.
   No JavaScript, for the same reason the contact dialog is a `:target`: the
   panel's markup is replaced wholesale every time it is opened or
   refreshed, and a handler bound to the old nodes would be pointing at
   elements that no longer exist.

   The rules that do the switching are *generated* into the page by
   _cycle_style(), because the cycle ids come from the licence server and
   this stylesheet is a module constant. What lives here is everything that
   does not depend on which cycles exist, including the selected tab's
   appearance — held in custom properties so the generated rules only have
   to say which label gets it, not what it looks like. */
.kx-cycle-scope {{
  --kx-tab-on-bg: linear-gradient(135deg, var(--kx-accent), var(--kx-accent-alt));
  --kx-tab-on-fg: #fff;
  --kx-tab-on-shadow: 0 6px 16px -8px {ACCENT}99;
}}

/* Visually hidden, not `display: none`: a removed input cannot be checked
   and cannot take focus, which would leave the tabs unusable by keyboard. */
.kx-cycle-radio {{
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}}

.kx-cycle-tabs {{
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 4px;
  width: fit-content;
  max-width: 100%;
  /* The bottom margin clears the "Most popular" flag, which straddles its
     card's top edge and so reaches 11px above the grid. */
  margin: 18px auto 16px;
  padding: 4px;
  border: 1px solid var(--kx-border);
  border-radius: 999px;
  background: var(--kx-sunken);
}}

/* `!important` on the things Gradio also has an opinion about for `label`:
   this markup sits inside one of its blocks, and a tab that inherits the
   form-label colour is unreadable against the accent fill. The generated
   rules win over these on id specificity, so the selected tab still gets
   its own colours. */
.kx-cycle-tabs label {{
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin: 0 !important;
  padding: 7px 15px !important;
  border-radius: 999px;
  font-size: .8rem !important;
  font-weight: 600 !important;
  color: var(--kx-muted) !important;
  background: transparent;
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
  transition: background .15s ease, color .15s ease;
}}
.kx-cycle-tabs label:hover {{ color: var(--kx-text) !important; }}

/* The reason the tabs exist. A cycle that saves money says so on the tab
   itself rather than only inside the cards, so the offer is visible before
   anyone thinks to click — and it keeps its accent colour on the unselected
   tabs, which is where it has work to do. */
.kx-cycle-save {{
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
  font-size: .62rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
}}
.dark .kx-cycle-save {{ color: #b9bcfb; }}

/* One block per cycle in every card; the checked radio reveals exactly one.
   The first cycle's block is the one shown by a plain stylesheet, and the
   generated rules swap it for another — so if those rules never arrive the
   page falls back to a plain monthly price list rather than to a card with
   no price on it at all. */
.kx-price {{ display: none; }}
.kx-price-first {{ display: block; }}

/* `align-items` is left at its `stretch` default so every card is as tall
   as the tallest in its row. The cards are themselves columns, so the
   extra height lands where the CTA's `margin-top: auto` puts it — as space
   above the button — and the content above stays top-aligned and reading
   straight across.

   The padding-top is headroom for the "Most Popular" flag, which is
   absolutely positioned over its card's top edge and would otherwise be
   clipped by the panel above. */
.kx-plan-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
  gap: 16px;
  padding-top: 12px;
}}

.kx-plan {{
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 20px;
  border: 1px solid var(--kx-border);
  border-radius: 16px;
  background: var(--kx-surface);
  box-shadow: var(--kx-shadow-sm);
}}

/* One card in the Prompt Library. Built from real Gradio components, not
   markup like the plan cards above, because each one carries a button that
   loads its settings into another tab — so this styles a gr.Column rather
   than an <article>, and the surface/border/radius are matched to
   .kx-plan by hand instead of shared.

   The Column is toggled visible per page. `height: 100%` on a flex column
   with the button pushed down by margin-top:auto is what keeps a row of
   three cards bottom-aligned when their prompts differ in length. */
.kx-prompt-card {{
  padding: 14px 16px;
  border: 1px solid var(--kx-border);
  border-radius: 14px;
  background: var(--kx-surface);
  box-shadow: var(--kx-shadow-sm);
}}
.kx-prompt-card:hover {{
  border-color: {ACCENT}59;
  box-shadow: var(--kx-shadow-md);
}}

/* The prompt itself, as a blockquote. Clamped to six lines: a card is a
   summary, and the whole text arrives in the tab when Use is pressed. */
.kx-prompt-card blockquote {{
  margin: 8px 0;
  padding-left: 10px;
  border-left: 2px solid {ACCENT}59;
  color: var(--kx-muted);
  font-size: 0.9rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}

/* The settings chips — `8 steps`, `CFG 1.0`, `3:4`. Inline code is what
   markdown gives us for free, so it is restyled here rather than being
   built out of spans the Markdown component would escape. */
.kx-prompt-card code {{
  padding: 1px 6px;
  border-radius: 6px;
  border: 1px solid var(--kx-border);
  background: var(--kx-sunken);
  font-size: 0.72rem;
  white-space: nowrap;
}}

/* Buttons sit at the bottom of their card whatever the prompt's length. */
.kx-prompt-card button {{
  margin-top: auto;
}}

/* The recommended tier (plan.is_popular). Brighter border, a soft accent
   glow and a hairline along the top edge — enough to read as "start here"
   at a glance without the scale-up that would break the row's alignment.
   Deliberately restrained: this is a card among four, not a billboard. */
.kx-plan-popular {{
  border-color: {ACCENT}66;
  box-shadow: 0 0 0 1px {ACCENT}33, 0 10px 30px -14px {ACCENT}59,
              var(--kx-shadow-md);
}}
.dark .kx-plan-popular {{
  box-shadow: 0 0 0 1px {ACCENT}40, 0 12px 34px -14px {ACCENT}4d,
              var(--kx-shadow-md);
}}

/* The accent hairline. A pseudo-element inset to the border radius rather
   than a border-top, which would square the corners off. */
.kx-plan-popular::before {{
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  border-radius: 16px 16px 0 0;
  background: linear-gradient(90deg, var(--kx-accent), var(--kx-accent-alt));
}}

/* The flag itself, straddling the top edge. */
.kx-plan-flag {{
  position: absolute;
  top: -11px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 1;
  padding: 4px 12px;
  border-radius: 999px;
  background: linear-gradient(135deg, var(--kx-accent), var(--kx-accent-alt));
  color: #fff;
  font-size: .62rem;
  font-weight: 700;
  letter-spacing: .07em;
  text-transform: uppercase;
  white-space: nowrap;
  box-shadow: 0 4px 12px -4px {ACCENT}80;
}}

/* The tier this licence is actually on. An accent ring rather than the
   usual scale-up: these cards sit in a grid whose tops are aligned, and
   lifting one breaks that alignment to say something the badge already
   says in words.

   After .kx-plan-popular so that a licence sitting on the recommended tier
   gets the current-plan ring — which is the more useful of the two facts
   once you are on it. The flag and the header badge both still show. */
.kx-plan-current {{
  border-color: var(--kx-accent);
  box-shadow: 0 0 0 1px var(--kx-accent), var(--kx-shadow-md);
}}

.kx-plan-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}}

.kx-plan-head h3 {{
  margin: 0 !important;
  font-size: 1rem !important;
  font-weight: 650 !important;
  color: var(--kx-text) !important;
}}

.kx-plan-badge {{
  flex: none;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
  font-size: .65rem;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
}}
.dark .kx-plan-badge {{ color: #b9bcfb; }}

/* The floor is two lines (1.55 line-height × 2) so a one-line description
   still pushes the price down to where the two-line cards put theirs, and
   the row of prices reads straight across. Only a floor: a description long
   enough to wrap further still gets the room, it just takes its card's
   price with it. */
.kx-plan-desc {{
  margin: 6px 0 0 !important;
  min-height: 3.1em;
  font-size: .8rem !important;
  line-height: 1.55;
  color: var(--kx-muted) !important;
}}

/* The figure is the thing being compared across four cards, so it gets the
   size and the only warm colour on the page. Amber as text rather than a
   filled gold block: on a near-black surface a solid gold panel reads as a
   promotional sticker, and it would fight the accent that already marks
   the recommended tier. */
.kx-plan-price {{
  margin: 14px 0 0 !important;
  display: flex;
  align-items: baseline;
  gap: 5px;
}}
.kx-plan-price b {{
  font-size: 2.15rem;
  font-weight: 700;
  line-height: 1.1;
  letter-spacing: -.025em;
  color: var(--kx-price);
}}
.kx-plan-price span {{
  font-size: .82rem;
  font-weight: 500;
  color: var(--kx-muted);
}}
.kx-plan-poa {{
  font-size: 1.05rem !important;
  font-weight: 600;
  color: var(--kx-muted) !important;
}}

/* What the headline figure actually bills — "₹5,750 billed yearly" — plus
   the saving, which is the whole argument for the longer cycle and so is
   the one thing on this line that is not faint. */
.kx-plan-billed {{
  margin: 5px 0 0 !important;
  font-size: .74rem !important;
  line-height: 1.5;
  color: var(--kx-faint) !important;
}}

.kx-plan-saving {{
  color: var(--kx-accent) !important;
  font-weight: 600;
}}
.dark .kx-plan-saving {{ color: #b9bcfb !important; }}

/* The feature list is the page — a customer reads it to find out what a
   tier actually gets them — so it gets the room, and the rule above it
   separates it from the price without a second card. */
.kx-plan-feats {{
  margin: 16px 0 0 !important;
  padding: 16px 0 0 !important;
  border-top: 1px solid var(--kx-border-soft);
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}}

.kx-plan-feats li {{
  display: flex;
  align-items: flex-start;
  gap: 9px;
  margin: 0 !important;
}}

.kx-tick {{
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 1px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
  font-size: .6rem;
  font-weight: 700;
}}
.dark .kx-tick {{ color: #b9bcfb; }}

.kx-feat {{ display: flex; flex-direction: column; gap: 1px; min-width: 0; }}
.kx-feat b {{ font-size: .78rem; font-weight: 600; color: var(--kx-text); }}
/* <i> for the one-line description, restyled upright: it is the semantic
   "different voice" tag and keeps the markup one element per row. */
.kx-feat i {{
  font-style: normal;
  font-size: .72rem;
  line-height: 1.5;
  color: var(--kx-faint);
}}

.kx-plan-empty {{ font-size: .78rem; color: var(--kx-faint); }}

/* `margin-top: auto` is what makes the equal-height cards work: it eats
   whatever slack the tallest card in the row created, so every button
   lands on the same line no matter how many features are listed above it.
   The 18px top margin is a floor for the card that *is* the tallest, where
   there is no slack to eat. */
.kx-plan-cta {{
  margin-top: auto;
  padding-top: 18px;
}}

.kx-plan-btn {{
  display: block;
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid var(--kx-border);
  background: transparent;
  color: var(--kx-text);
  font-size: .82rem;
  font-weight: 600;
  text-align: center;
  text-decoration: none !important;
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease, color .15s ease;
}}
.kx-plan-btn:hover {{
  border-color: var(--kx-accent);
  background: var(--kx-accent-soft);
  color: var(--kx-text);
}}

/* The recommended tier's button, filled so the eye lands on it first. */
.kx-plan-btn-primary {{
  border-color: transparent;
  background: linear-gradient(135deg, var(--kx-accent), var(--kx-accent-alt));
  color: #fff;
  box-shadow: 0 6px 18px -8px {ACCENT}99;
}}
.kx-plan-btn-primary:hover {{
  border-color: transparent;
  background: linear-gradient(135deg, var(--kx-accent-alt), var(--kx-accent));
  color: #fff;
}}

/* The tier already held. Not a link and not clickable — there is nothing
   to ask for — so it is a <span> styled as a flat, quiet slab. It keeps
   the button row aligned across the four cards without pretending to be
   an action. */
.kx-plan-btn-current {{
  border-style: dashed;
  border-color: var(--kx-accent);
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
  cursor: default;
}}
.dark .kx-plan-btn-current {{ color: #b9bcfb; }}

/* --------------------------------------------------- contact dialog */
/* Opened by the plan buttons, closed by the backdrop or the Close link.
   Driven by :target rather than JavaScript: this whole page is one HTML
   string handed to gr.HTML, and Gradio strips <script> out of it, so a
   CSS-only dialog is the only kind that survives. The cost is a URL hash
   while it is open, which is invisible on a Gradio share link. */
.kx-modal {{
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(8, 10, 16, .62);
  backdrop-filter: blur(3px);
}}
.kx-modal:target {{ display: flex; }}

/* Covers the viewport behind the card so a click anywhere outside closes
   it, which is the gesture people try first. */
.kx-modal-scrim {{
  position: absolute;
  inset: 0;
  cursor: default;
}}

.kx-modal-card {{
  position: relative;
  width: min(420px, 100%);
  padding: 24px;
  border: 1px solid var(--kx-border);
  border-radius: 16px;
  background: var(--kx-surface);
  box-shadow: var(--kx-shadow-lg);
  text-align: center;
}}

.kx-modal-card h3 {{
  margin: 0 0 8px !important;
  font-size: 1.05rem !important;
  font-weight: 650 !important;
  color: var(--kx-text) !important;
}}

.kx-modal-card p {{
  margin: 0 !important;
  font-size: .84rem !important;
  line-height: 1.6;
  color: var(--kx-muted) !important;
}}

.kx-modal-actions {{
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin-top: 18px;
}}

.kx-modal-close {{
  font-size: .78rem;
  color: var(--kx-faint) !important;
  text-decoration: none !important;
}}
.kx-modal-close:hover {{ color: var(--kx-muted) !important; }}

.kx-pricing-foot {{
  margin: 20px 0 0 !important;
  max-width: 76ch;
  font-size: .74rem !important;
  line-height: 1.6;
  color: var(--kx-faint) !important;
}}

/* ------------------------------------------------------- feature showcase */
/* What the app actually does, under the plan cards: one section per granted
   feature, each a short essay and a set of pictures. See showcase.py for
   where the copy and the images come from.

   The whole thing is markup and CSS with no script, for the reason the
   contact dialog gives above — gr.HTML strips <script> — so the two
   interactive parts are done the same way: the zoom view is a `:target`
   dialog, and nothing else needs state at all.

   Sizing rule for the blocks below: pictures that have to line up with
   each other (a before/after pair, a row of variants) go in a fixed
   aspect box and are cropped to fill it; pictures in a collage keep their
   own shape, and the renderer emits their real width/height so the
   masonry does not reflow as they load. */
.kx-show {{ max-width: 1180px; margin: 64px auto 0; }}

/* The seam between the plan grid and the showcase — a full-width rule with
   the eyebrow sitting on it, so the page reads as two chapters rather than
   as a card grid that kept going. */
.kx-show-seam {{
  position: relative;
  margin: 0 0 40px;
  border-top: 1px solid var(--kx-border);
  text-align: center;
}}
.kx-show-seam span {{
  position: relative;
  top: -.7em;
  padding: 0 16px;
  background: var(--body-background-fill);
  color: var(--kx-faint);
  font-size: .68rem;
  font-weight: 700;
  letter-spacing: .16em;
  text-transform: uppercase;
}}

/* ------------------------------------------------------------ intro */
.kx-show-intro {{ max-width: 780px; margin: 0 auto 34px; text-align: center; }}

.kx-show-intro h2 {{
  margin: 0 0 14px !important;
  font-size: clamp(1.7rem, 3.4vw, 2.5rem) !important;
  font-weight: 800 !important;
  line-height: 1.15;
  letter-spacing: -.025em;
  color: var(--kx-text) !important;
}}
/* The one gradient-filled phrase on the page. `em` carries no emphasis
   here — it is the hook the copy marks the accent half of the title with. */
/* Solid accent, not a gradient clipped to the glyphs. `background-clip: text`
   needs `color: transparent`, and the moment anything stops the gradient
   from painting — an unsupported property, a variable that did not resolve,
   a reset that drops the background — the headline is not merely
   un-styled, it is invisible, because the page shows straight through the
   letters. Nothing else in this stylesheet risks that, and a headline is
   the last place to start. */
.kx-show-intro h2 em {{ font-style: normal; color: var(--kx-accent) !important; }}
.dark .kx-show-intro h2 em {{ color: #b9bcfb !important; }}
.kx-show-intro p {{
  margin: 0 auto 12px !important;
  max-width: 68ch;
  font-size: .95rem !important;
  line-height: 1.75;
  color: var(--kx-muted) !important;
}}

/* Four figures in one bordered strip rather than four cards: boxed stats
   this close to the plan grid read as more pricing.

   Named kx-show-stats, not kx-stats: that shorter name is already the
   header's and the footer's pill row (see .kx-stats far above), and a
   second unscoped rule for it turned both of those into a bordered grid
   with `margin: ... auto`, which centred the licence pill in the app bar
   instead of leaving it at the right edge. Every other piece of this
   section carries the kx-show- prefix for exactly this reason. */
.kx-show-stats {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  margin: 30px auto 0;
  border: 1px solid var(--kx-border);
  border-radius: 16px;
  background: var(--kx-surface);
  overflow: hidden;
}}
.kx-show-stat {{ padding: 18px 14px; border-right: 1px solid var(--kx-border-soft); }}
.kx-show-stat:last-child {{ border-right: 0; }}
/* Solid, for the reason given at .kx-show-intro h2 em — these numbers had
   the same clipped-gradient fill and so the same way of vanishing. */
.kx-show-stat b {{
  display: block;
  font-size: 1.5rem;
  font-weight: 800;
  letter-spacing: -.02em;
  color: var(--kx-accent);
}}
.dark .kx-show-stat b {{ color: #b9bcfb; }}
.kx-show-stat span {{
  display: block;
  font-size: .72rem;
  line-height: 1.45;
  color: var(--kx-faint);
}}

/* --------------------------------------------------------- jump chips */
/* The section is long by design, so it opens with its own contents list,
   in the same order as the tabs. */
.kx-jump {{
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 7px;
  margin: 0 auto 56px;
}}
.kx-jump a {{
  padding: 6px 13px;
  border: 1px solid var(--kx-border);
  border-radius: 999px;
  background: var(--kx-surface);
  color: var(--kx-muted) !important;
  font-size: .76rem;
  font-weight: 600;
  text-decoration: none !important;
  transition: border-color .15s ease, color .15s ease, transform .15s ease;
}}
.kx-jump a:hover {{
  border-color: var(--kx-accent);
  color: var(--kx-accent) !important;
  transform: translateY(-1px);
}}

/* ------------------------------------------------------------ section */
.kx-feat {{ margin: 0 0 76px; scroll-margin-top: 24px; }}

.kx-feat-head {{
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0 20px;
  align-items: start;
  margin-bottom: 24px;
}}
/* Decoration with a job: it says how much section is left, which a run of
   galleries otherwise hides. */
.kx-feat-no {{
  grid-row: span 2;
  font-size: 2.6rem;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -.04em;
  color: var(--kx-border);
  font-variant-numeric: tabular-nums;
}}
.dark .kx-feat-no {{ color: #2c3340; }}

.kx-feat-eyebrow {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}}
.kx-feat-tab {{
  font-size: .74rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: var(--kx-accent);
}}
.kx-chip-on {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(16, 185, 129, .12);
  color: var(--kx-ok);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
}}
.kx-feat-head h3 {{
  grid-column: 2;
  margin: 0 0 10px !important;
  font-size: clamp(1.25rem, 2.4vw, 1.7rem) !important;
  font-weight: 700 !important;
  letter-spacing: -.02em;
  line-height: 1.25;
  color: var(--kx-text) !important;
}}
.kx-feat-body {{ grid-column: 2; }}
.kx-feat-body p {{
  margin: 0 0 12px !important;
  max-width: 72ch;
  font-size: .88rem !important;
  line-height: 1.75;
  color: var(--kx-muted) !important;
}}
.kx-feat-body p:last-child {{ margin-bottom: 0 !important; }}

/* The capability list. Chips rather than bullets: they are short, there are
   a lot of them, and they wrap into whatever width the prose leaves. */
.kx-hl {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 16px; }}
.kx-hl span {{
  padding: 4px 11px;
  border: 1px solid var(--kx-border);
  border-radius: 8px;
  background: var(--kx-surface);
  font-size: .73rem;
  color: var(--kx-muted);
}}
.kx-hl span::before {{ content: "▪"; margin-right: 6px; color: var(--kx-accent); }}

/* ------------------------------------------------------- block shells */
.kx-block {{ margin: 28px 0 0; }}

.kx-block-cap {{
  margin: 12px 2px 0 !important;
  font-size: .78rem !important;
  font-style: italic;
  line-height: 1.6;
  color: var(--kx-faint) !important;
}}
.kx-block-cap b {{ font-style: normal; font-weight: 600; color: var(--kx-muted); }}

/* Text beside pictures instead of above them, used on alternate sections so
   the page does not become a dozen identical stacks. */
.kx-split {{
  display: grid;
  grid-template-columns: 1fr 1.25fr;
  align-items: center;
  gap: 34px;
}}
.kx-split-reverse > :first-child {{ order: 2; }}
.kx-split h4 {{
  margin: 0 0 8px !important;
  font-size: 1rem !important;
  font-weight: 700 !important;
  letter-spacing: -.01em;
  color: var(--kx-text) !important;
}}
.kx-split p {{
  margin: 0 !important;
  font-size: .84rem !important;
  line-height: 1.75;
  color: var(--kx-muted) !important;
}}

/* Two blocks side by side — a feature where one example proves nothing. */
.kx-pair {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
  gap: 26px;
}}

/* -------------------------------------------------------------- hero */
.kx-hero {{
  position: relative;
  border-radius: 20px;
  overflow: hidden;
  box-shadow: var(--kx-shadow-lg);
}}
.kx-hero .kx-shot {{ border: 0; border-radius: 0; box-shadow: none; }}
.kx-hero-text {{
  position: absolute;
  inset: auto 0 0 0;
  padding: 46px 26px 22px;
  background: linear-gradient(to top, rgba(6, 8, 14, .88), rgba(6, 8, 14, 0));
  color: #fff;
  pointer-events: none;
}}
.kx-hero-text b {{
  display: block;
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -.01em;
}}
.kx-hero-text span {{ font-size: .8rem; color: rgba(255, 255, 255, .78); }}

/* ----------------------------------------------------------- compare */
/* Before and after as two plates rather than a drag slider: a slider hides
   half the result at all times, and most of these edits change the whole
   frame rather than one region of it. The arrow is what makes the pair
   read left-to-right instead of as two unrelated pictures. */
.kx-compare {{
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 14px;
}}
.kx-arrow {{
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--kx-border);
  background: var(--kx-surface);
  box-shadow: var(--kx-shadow-sm);
  color: var(--kx-accent);
  font-size: .95rem;
  font-weight: 700;
}}

/* --------------------------------------------------------------- fan */
/* One source, N results — the "same photograph, three directions" row. The
   source keeps an accent ring so the eye starts there. */
.kx-fan {{
  display: grid;
  grid-template-columns: minmax(180px, .95fr) auto 3fr;
  align-items: center;
  gap: 16px;
}}
.kx-fan-out {{ display: grid; gap: 12px; }}
.kx-fan-src .kx-shot {{ outline: 2px solid var(--kx-accent); outline-offset: 3px; }}

/* Also the shape the row and inpaint blocks use on their own. */
.kx-row {{ display: grid; gap: 12px; }}
.kx-cols-2 {{ grid-template-columns: repeat(2, 1fr); }}
.kx-cols-3 {{ grid-template-columns: repeat(3, 1fr); }}
.kx-cols-4 {{ grid-template-columns: repeat(4, 1fr); }}
.kx-cols-5 {{ grid-template-columns: repeat(5, 1fr); }}

/* ----------------------------------------------------------- collage */
/* The deliberately uneven one. CSS columns rather than a grid so tiles of
   different heights pack without leaving holes, and a repeating nudge keyed
   off :nth-child so nothing lines up with its neighbour. The rotation comes
   off on hover, which is what makes it read as arranged rather than broken. */
.kx-collage {{ columns: 4 210px; column-gap: 14px; }}
.kx-collage .kx-shot {{
  break-inside: avoid;
  margin: 0 0 14px;
  transition: transform .22s ease, box-shadow .22s ease;
}}
.kx-collage .kx-shot:nth-child(4n+1) {{ transform: rotate(-1.1deg); }}
.kx-collage .kx-shot:nth-child(4n+2) {{ transform: rotate(.8deg) translateY(6px); }}
.kx-collage .kx-shot:nth-child(4n+3) {{ transform: rotate(1.4deg) translateY(-4px); }}
.kx-collage .kx-shot:nth-child(4n+4) {{ transform: rotate(-.6deg) translateY(3px); }}
.kx-collage .kx-shot:hover {{
  transform: rotate(0) scale(1.03);
  box-shadow: var(--kx-shadow-lg);
  position: relative;
  z-index: 2;
}}

/* The straight variant, for blocks where the grid itself is the subject
   (the prompt library) and a skew would read as sloppy. */
.kx-collage-flat .kx-shot {{ transform: none !important; }}
.kx-collage-flat .kx-shot:hover {{ transform: scale(1.03) !important; }}

/* ------------------------------------------------------------- strip */
.kx-strip {{
  display: flex;
  gap: 12px;
  overflow-x: auto;
  padding-bottom: 10px;
  scroll-snap-type: x mandatory;
  scrollbar-width: thin;
}}
.kx-strip .kx-shot {{ flex: 0 0 230px; scroll-snap-align: start; }}
.kx-strip-note {{
  margin: 4px 0 0 !important;
  font-size: .72rem !important;
  color: var(--kx-faint) !important;
}}

/* -------------------------------------------------------------- shot */
/* One picture, wherever it appears. The frame, the corner pills and the
   zoom affordance live here so every block gets them for free. */
.kx-shot {{
  position: relative;
  display: block;
  margin: 0;
  border-radius: 14px;
  overflow: hidden;
  background: var(--kx-sunken);
  border: 1px solid var(--kx-border);
  box-shadow: var(--kx-shadow-sm);
}}
a.kx-shot {{ cursor: zoom-in; text-decoration: none !important; }}
.kx-shot img,
.kx-shot video {{
  display: block;
  width: 100%;
  height: auto;
  border-radius: 0;
}}
/* Fixed-shape blocks carry the aspect ratio on the frame and crop what is
   inside it to fill, so a pair of pictures that were not shot at the same
   size still line up. The collage sets no ratio and its pictures keep their
   own — which is why it is the one block that measures its files. */
.kx-shot-fixed > img,
.kx-shot-fixed > video,
.kx-shot-fixed > .kx-ph {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}}

.kx-tag {{
  position: absolute;
  top: 8px;
  left: 8px;
  z-index: 2;
  padding: 3px 9px;
  border-radius: 999px;
  background: rgba(8, 10, 16, .62);
  backdrop-filter: blur(6px);
  color: #fff;
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .05em;
  text-transform: uppercase;
}}
.kx-tag-accent {{ left: auto; right: 8px; background: {ACCENT}d9; }}
.kx-fan-src .kx-tag-accent,
.kx-shot-lead .kx-tag-accent {{ left: 8px; right: auto; }}

/* The prompt that produced a picture. Positioned out of flow so a long
   caption can never change a tile's height, which in a masonry would move
   every tile below it. */
.kx-shot-cap {{
  position: absolute;
  inset: auto 0 0 0;
  padding: 22px 11px 9px;
  background: linear-gradient(to top, rgba(6, 8, 14, .9), transparent);
  color: rgba(255, 255, 255, .92) !important;
  font-size: .7rem;
  line-height: 1.4;
  opacity: 0;
  transform: translateY(6px);
  transition: opacity .2s ease, transform .2s ease;
}}
.kx-shot:hover .kx-shot-cap {{ opacity: 1; transform: none; }}

/* ------------------------------------------------------ placeholders */
/* What a slot renders as until a file exists at its path — see showcase.py.
   It names the file it is waiting for, which is what makes an unfinished
   folder self-documenting; the hue is picked from that path so a tile keeps
   its colour as neighbours are added. */
.kx-ph {{
  position: relative;
  display: grid;
  place-items: center;
  aspect-ratio: 4 / 3;
  background:
    radial-gradient(120% 90% at 20% 0%, rgba(255, 255, 255, .28), transparent 55%),
    linear-gradient(145deg,
      hsl(var(--kx-h, 250) 72% 64%),
      hsl(calc(var(--kx-h, 250) + 38) 72% 56%));
}}
.dark .kx-ph {{
  background:
    radial-gradient(120% 90% at 20% 0%, rgba(255, 255, 255, .16), transparent 55%),
    linear-gradient(145deg,
      hsl(var(--kx-h, 250) 56% 46%),
      hsl(calc(var(--kx-h, 250) + 38) 56% 38%));
}}
/* A faint weave, so a dozen of these together do not read as flat swatches. */
.kx-ph::before {{
  content: "";
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(45deg,
    rgba(255, 255, 255, .07) 0 2px, transparent 2px 9px);
}}
.kx-ph-glyph {{
  position: relative;
  font-size: 1.5rem;
  opacity: .5;
  filter: grayscale(1) brightness(2.4);
}}
.kx-ph-path {{
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 8px;
  padding: 3px 7px;
  border-radius: 6px;
  background: rgba(8, 10, 16, .5);
  color: rgba(255, 255, 255, .86);
  font-family: var(--font-mono);
  font-size: .6rem;
  line-height: 1.35;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}}

/* --------------------------------------------------------- not in plan */
/* A feature this licence does not grant. Nothing here dims it, and that is
   deliberate: this section sits under the price list to argue for the
   upgrade, and a greyed-out screenshot argues against it. What marks it is
   a chip in the accent colour and a line at the end of the section saying
   how to get it — the section reads as an offer rather than as a locked
   door. */
.kx-chip-off {{
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--kx-accent-soft);
  color: var(--kx-accent);
  font-size: .64rem;
  font-weight: 700;
  letter-spacing: .04em;
  text-transform: uppercase;
}}
.dark .kx-chip-off {{ color: #b9bcfb; }}

/* The per-section upgrade line. Quiet by default — one of these under every
   ungranted section, and a page of loud banners reads as a nag rather than
   as a catalogue — but it is a real link, to the plan grid at the top of
   the page. */
.kx-feat-up {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
  padding: 11px 15px;
  border: 1px solid var(--kx-border);
  border-left: 3px solid var(--kx-accent);
  border-radius: 0 12px 12px 0;
  background: var(--kx-sunken);
  font-size: .8rem;
  color: var(--kx-muted);
}}
.kx-feat-up a {{
  margin-left: auto;
  padding: 5px 13px;
  border-radius: 999px;
  background: linear-gradient(135deg, var(--kx-accent), var(--kx-accent-alt));
  color: #fff !important;
  font-size: .75rem;
  font-weight: 600;
  text-decoration: none !important;
  white-space: nowrap;
}}

/* ------------------------------------------------------------ closing */
.kx-show-cta {{
  margin: 8px auto 0;
  padding: 34px 28px;
  border: 1px solid var(--kx-border);
  border-radius: 20px;
  background:
    linear-gradient(135deg, {ACCENT}1a, {ACCENT_ALT}12),
    var(--kx-surface);
  text-align: center;
}}
.kx-show-cta h3 {{
  margin: 0 0 8px !important;
  font-size: 1.25rem !important;
  font-weight: 700 !important;
  letter-spacing: -.02em;
  color: var(--kx-text) !important;
}}
.kx-show-cta p {{
  margin: 0 auto 18px !important;
  max-width: 60ch;
  font-size: .85rem !important;
  line-height: 1.7;
  color: var(--kx-muted) !important;
}}
.kx-show-cta .kx-plan-btn {{
  display: inline-block;
  width: auto;
  margin: 0;
  padding: 10px 26px;
}}

/* ----------------------------------------------------------- lightbox */
/* Click a picture to see it whole. A `:target` dialog, exactly like the
   contact one above and for the same reason — there is no script on this
   page. The full-size copy inside stays lazy, so a section of forty
   pictures still only fetches what is on screen. */
.kx-lb {{
  position: fixed;
  inset: 0;
  z-index: 1001;
  display: none;
  place-items: center;
  padding: 32px;
  background: rgba(6, 8, 14, .84);
  backdrop-filter: blur(4px);
}}
.kx-lb:target {{ display: grid; }}
.kx-lb-scrim {{ position: absolute; inset: 0; cursor: default; }}
.kx-lb-card {{
  position: relative;
  width: min(1000px, 92vw);
  max-height: 88vh;
  border-radius: 16px;
  overflow: hidden;
  background: var(--kx-surface);
  box-shadow: 0 30px 80px -20px rgba(0, 0, 0, .8);
}}
.kx-lb-card img,
.kx-lb-card video {{
  display: block;
  width: 100%;
  max-height: 78vh;
  object-fit: contain;
  background: #06080e;
}}
.kx-lb-cap {{
  margin: 0 !important;
  padding: 11px 15px;
  font-size: .78rem !important;
  color: var(--kx-muted) !important;
}}
.kx-lb-close {{
  position: absolute;
  top: 10px;
  right: 12px;
  z-index: 2;
  padding: 5px 13px;
  border-radius: 999px;
  background: rgba(8, 10, 16, .6);
  backdrop-filter: blur(6px);
  color: #fff !important;
  font-size: .78rem;
  font-weight: 600;
  text-decoration: none !important;
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
/* Clicking a tile now opens the full-size original below the grid rather
   than Gradio's lightbox, so the tiles have to look clickable. */
.gradio-container .gallery-item {{ cursor: pointer; }}
/* Square tiles. The grid mixes portrait, landscape and (until video
   posters exist) video, and object-fit: cover only crops within whatever
   box it is given — without a fixed ratio a page of mixed shapes leaves
   the rows ragged. */
.gradio-container .grid-wrap .gallery-item {{ aspect-ratio: 1 / 1; }}

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
  #kx-header {{ position: static; flex-wrap: wrap; }}
  #kx-header .kx-bar {{ align-items: flex-start; }}
  .kx-panel {{ padding: 14px !important; }}
}}

@media (max-width: 860px) {{
  .gradio-container .row {{ flex-wrap: wrap; }}
  .kx-panel, .kx-panel-out {{ flex: 1 1 100% !important; min-width: 100% !important; }}
  /* The Gallery tab, smaller rather than rearranged — it is one column
     at every width, so a phone needs a couple of numbers rather than a
     second layout.

     Taller than it looks like it should be, and deliberately: a phone
     screen is portrait and so is nearly everything this app generates,
     so the stage has to be about half again as tall as it is wide for
     the picture to fill it. At anything less the picture is pinched
     into the middle with the width of the screen wasted either side —
     which is exactly what a stage in the shape of a desktop one did. */
  .kx-stage {{ --kx-stage-h: 62vh; }}
  .kx-strip {{ --kx-tile: 62px; }}
  /* The corner Gradio's own buttons sit in is a smaller one here. */
  .kx-zones {{ inset: 36px 0 0 0; }}
}}

/* The showcase blocks, which are laid out by their own grids rather than by
   Gradio rows and so need their own steps. The arrows turn to point down as
   the pairs they sit between stack. */
@media (max-width: 900px) {{
  .kx-split {{ grid-template-columns: 1fr; }}
  .kx-split-reverse > :first-child {{ order: 0; }}
  .kx-fan, .kx-compare {{ grid-template-columns: 1fr; }}
  .kx-fan .kx-arrow, .kx-compare .kx-arrow {{
    transform: rotate(90deg);
    justify-self: center;
  }}
  .kx-collage {{ columns: 2 150px; }}
}}

@media (max-width: 620px) {{
  .kx-feat-head {{ grid-template-columns: 1fr; }}
  .kx-feat-no {{ display: none; }}
  .kx-feat-head h3, .kx-feat-body {{ grid-column: 1; }}
  .kx-cols-3, .kx-cols-4, .kx-cols-5 {{ grid-template-columns: repeat(2, 1fr); }}
  .kx-show-stat {{ border-right: 0; border-bottom: 1px solid var(--kx-border-soft); }}
  .kx-show {{ margin-top: 44px; }}
}}

@media (prefers-reduced-motion: reduce) {{
  .gradio-container * {{
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }}
  .gradio-container .gallery-item:hover {{ transform: none; }}
  /* The collage's tilt is decoration, and it is the one thing here that
     moves without being asked. */
  .kx-collage .kx-shot {{ transform: none !important; }}
}}
"""


# --------------------------------------------------------------------------
# <head> — favicon, so a pinned tab is identifiable
# --------------------------------------------------------------------------
# Inline SVG data URI rather than launch(favicon_path=...): a path would be
# one more data file for the Nuitka build to carry.
#
# The flame is the Ember mark (assets/branding/ember-logo.svg) with its
# outline decimated — at the 16-32px a favicon is actually drawn at, the
# dropped detail is a fraction of a pixel, and the full curve would be five
# times this much text sitting in every page's <head>. The full-fidelity
# mark lives in the asset file; use that one anywhere it renders large.
_FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop offset='0' stop-color='%236366f1'/%3E"
    "%3Cstop offset='1' stop-color='%238b5cf6'/%3E%3C/linearGradient%3E%3C/defs%3E"
    "%3Crect width='100' height='100' rx='24' fill='url(%23g)'/%3E"
    "%3Cpath fill='%23fff' d='"
    "M51.1 18.7L47.5 22.2L46.7 23.4L45.5 25.8L44.8 27.6L44.8 28L44.6 "
    "28.1L44.3 31.1L44.4 33.4L44.8 35.7L45.7 38.8L46.8 40.8L49.6 "
    "44.7L53.4 49.1L54 50.1L50.6 47.6L46.7 43.8L44.2 40.9L43.4 "
    "39.6L42.3 37.3L41.4 33.1L40.3 34.1L39.1 35.6L37.4 39.4L37 "
    "42.2L37.2 44.4L37.8 47L39.2 50L40.3 51.7L42.4 54.3L42.5 54.7L43.2 "
    "55.3L43.6 56L43.8 56.1L45.3 58L44.6 58.1L42 57L39.8 55.5L38 "
    "53.7L36.7 52.1L36.8 52L35.9 50.5L35.1 48.4L34.5 45.4L34.3 "
    "45.2L33.6 45.8L30.8 49.2L30.3 50.7L30.1 50.7L29.6 51.6L28.4 "
    "55.9L27.9 59.5L28.1 63.6L28.6 65.8L29.3 68L30.1 69.6L31.3 "
    "71.8L34.4 75.7L38.2 78.6L40.6 79.9L42.9 80.9L48 82L52.8 81.8L56.9 "
    "80.9L61.4 78.7L64.7 76.3L66.3 74.7L68.2 72.4L70 69.4L71.3 "
    "65.9L72.1 61.2L72.1 57.7L71.7 54.8L70.9 51.8L69.7 49L67.2 44.8L63 "
    "39.9L62.8 39.5L62.1 38.9L59.3 35.4L59 35.3L58.6 34.5L56.5 32L54.6 "
    "29.3L53.3 26.7L52.4 24L52.1 21.7L52.1 18L51.1 18.7Z"
    "M43.4 71.6L44.7 72.4L45.8 73.4L46.8 74.6L47.7 76.2L48.4 78.7L48.4 "
    "80.7L48.1 81.7L48 81.5L47.1 81L45.5 79.4L43.9 77L43.1 74.8L42.7 "
    "72.3L42.7 71.3L43.4 71.6Z"
    "'/%3E%3C/svg%3E"
)

HEAD = f"""
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<meta name="color-scheme" content="light dark">
<meta name="description" content="Ember — ComfyUI generation suite on RunPod">
"""


# --------------------------------------------------------------------------
# Page JS
# --------------------------------------------------------------------------
# Three conveniences, all additive: nothing here is required for any control
# to work, and each is written to no-op rather than throw if Gradio's DOM
# is not what it expects. The third — undo/redo on the prompt boxes — is
# the one that is not merely a convenience on a phone, where the browser
# offers no other way back from an edit.
# Raw, so a regex like /\s$/ reaches the browser as written rather than
# being read as a Python escape on the way past.
#
# **Self-executing, and it has to be.** `launch(js=...)` drops this string
# into a <script> tag verbatim — so a bare `() => {...}` is an expression
# that is evaluated and thrown away, and nothing in here ever runs. That is
# what happened between the Gradio 4 API (which called the function for us)
# and Gradio 6 (which does not): the whole blob went quietly dead, taking
# Ctrl+Enter and the copy-path pill with it, with no error anywhere.
#
# The guard is belt and braces: if a future Gradio goes back to calling it,
# the listeners below must not be registered twice.
JS = r"""
(() => {
  if (window.__kxPageJs) return;
  window.__kxPageJs = true;
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

  // Left/right arrows walk the Gallery tab's viewer, which is the
  // difference between browsing a few hundred generations and clicking
  // through them one at a time. The two buttons are found by id and only
  // ever clicked while they are on screen (offsetParent is null inside a
  // hidden tab), so this costs nothing and does nothing on any other tab.
  //
  // They are the off-screen pair (.kx-sr-nav), not the click zones over
  // the picture: the zones are hidden while a video is playing, and the
  // keys have to keep walking straight past one. Clipped rather than
  // display: none precisely so that on-screen test still means "the
  // Gallery tab is the tab you are looking at".
  document.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) {
      return;
    }
    // Never take an arrow key away from something being typed in.
    const active = document.activeElement;
    if (active && active.closest(
        "input, textarea, select, [contenteditable=true]")) return;
    const host = document.getElementById(
      event.key === "ArrowLeft" ? "kx-gallery-prev" : "kx-gallery-next");
    if (!host || !host.offsetParent) return;
    // Gradio hangs elem_id on the button in some versions and on its
    // wrapper in others — ask for both rather than guess.
    const button = host.tagName === "BUTTON" ? host
                                             : host.querySelector("button");
    if (!button || button.disabled) return;
    event.preventDefault();
    button.click();
  });

  // ------------------------------------------------------- the filmstrip
  // Two jobs on the Gallery tab's strip, both of them things CSS cannot
  // do on its own.
  //
  // **Lay it out as one scrolling row.** A gr.Gallery is a wrapping grid
  // inside a box of its own, and which element holds the tiles is
  // Gradio's business — it has a different class name in different
  // versions, and a stylesheet that guesses wrong silently leaves a
  // second screenful of pictures where a strip should be. So find a
  // tile, take its actual parent, and make *that* the row: no wrapping,
  // as wide as its contents. Everything between the row and the block is
  // then opened up so nothing clips it, and the block itself — the one
  // element here with a name of our own on it — is the scroller.
  const layOutStrip = (strip) => {
    const tile = strip.querySelector(".thumbnail-item, .gallery-item");
    const row = tile && tile.parentElement;
    if (!row || row === strip) return;
    Object.assign(row.style, {
      display: "flex", flexWrap: "nowrap", gridTemplateColumns: "none",
      width: "max-content", minWidth: "100%", maxWidth: "none",
      height: "auto", minHeight: "0", maxHeight: "none",
      overflow: "visible",
    });
    for (let box = row.parentElement; box && box !== strip;
         box = box.parentElement) {
      Object.assign(box.style, {
        width: "max-content", minWidth: "100%", maxWidth: "none",
        height: "auto", minHeight: "0", maxHeight: "none",
        overflow: "visible",
      });
    }
  };

  // **Keep the highlighted tile in view.** Stepping through the list
  // moves the highlight without touching the strip, so a walk of a dozen
  // files would otherwise leave the tile being shown off to the left of
  // a strip that never moved — and with it any sense of where in the
  // list you are.
  //
  // Scrolls the strip itself rather than calling scrollIntoView, which
  // would take the page with it and drag the picture off the top of the
  // screen. Only when the tile is near an edge: re-centring on a tile
  // someone has just clicked is the strip moving out from under the
  // pointer for no reason.
  const followStrip = (strip) => {
    const tile = strip.querySelector('.selected, [aria-selected="true"]');
    if (!tile) return;
    const box = tile.getBoundingClientRect();
    const view = strip.getBoundingClientRect();
    if (!box.width || !view.width) return;
    const edge = box.width * 0.75;
    if (box.left >= view.left + edge && box.right <= view.right - edge) {
      return;
    }
    strip.scrollBy({
      left: (box.left + box.width / 2) - (view.left + view.width / 2),
      behavior: "smooth",
    });
  };

  // The strip is inside a tab that is built with the page but may never
  // be opened, so this waits for the element rather than assuming it is
  // there when the script runs — and stops watching for it the moment it
  // is. Redraws are batched through one frame: Gradio rewrites the whole
  // grid when a page of tiles is added, which is a burst of mutations
  // for one move of the highlight.
  const watchStrip = () => {
    const strip = document.getElementById("kx-gallery-strip");
    if (!strip) return false;
    let queued = false;
    const redraw = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        layOutStrip(strip);
        followStrip(strip);
      });
    };
    // Styles are set on elements this observer is watching, so it is
    // told about class and nothing else — a style attribute in the
    // filter would have it wake itself up forever.
    new MutationObserver(redraw).observe(strip, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ["class"],
    });
    redraw();
    return true;
  };
  if (!watchStrip()) {
    const boot = new MutationObserver(() => {
      if (watchStrip()) boot.disconnect();
    });
    boot.observe(document.documentElement, {childList: true, subtree: true});
  }

  // Click the output-path pill in the footer to copy it. The pod's disk is
  // ephemeral, so this path gets typed into scp/rsync often enough to be
  // worth a click.
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

  // ------------------------------------------------------------------ undo
  // Undo and redo for every prompt box, as two buttons under it.
  //
  // A desktop browser gives a textarea its own undo stack and Ctrl+Z
  // reaches it. A phone keyboard has no Ctrl, and no mobile browser
  // exposes undo for a text field any other way — so a prompt edited on a
  // phone was simply not recoverable, which is the whole reason this
  // exists.
  //
  // The history is kept here rather than leaning on the browser's, for
  // three reasons. It is the only way a *button* can drive it. It survives
  // a value written by Gradio, so loading a preset, a recipe or a library
  // card over a prompt is undoable — the native stack knows nothing about
  // those. And the keys are routed through it too, so the buttons and
  // Ctrl+Z share one history instead of walking two that disagree the
  // moment either is used.
  const UNDO_COALESCE_MS = 450;   // a burst of typing is one entry
  const UNDO_DEPTH = 100;         // entries kept per box; prompts are small
  const undoStates = new WeakMap();

  const UNDO_ICONS = {
    Undo: '<svg viewBox="0 0 24 24" aria-hidden="true">'
        + '<path d="M9 14 4 9l5-5"/><path d="M4 9h8a6 6 0 0 1 0 12H9"/></svg>',
    Redo: '<svg viewBox="0 0 24 24" aria-hidden="true">'
        + '<path d="m15 14 5-5-5-5"/><path d="M20 9h-8a6 6 0 0 0 0 12h3"/>'
        + '</svg>',
  };

  function undoState(area) {
    let state = undoStates.get(area);
    if (!state) {
      state = { stack: [area.value], at: 0, typed: 0, applying: false,
                buttons: {} };
      undoStates.set(area, state);
    }
    return state;
  }

  // Gradio writes a value straight onto the element and fires no input
  // event, so the stack finds out lazily: anything that is not what we
  // last left there is someone else's edit, and becomes its own entry —
  // which is what makes "undo the preset I just loaded" work.
  function undoSync(area, state) {
    if (area.value === state.stack[state.at]) return;
    state.stack.length = state.at + 1;
    state.stack.push(area.value);
    state.at = state.stack.length - 1;
    state.typed = 0;
  }

  function undoRefresh(state) {
    const { Undo, Redo } = state.buttons;
    if (Undo) Undo.disabled = state.at <= 0;
    if (Redo) Redo.disabled = state.at >= state.stack.length - 1;
  }

  function undoStep(area, delta) {
    const state = undoState(area);
    undoSync(area, state);
    const next = state.at + delta;
    if (next < 0 || next >= state.stack.length) {
      undoRefresh(state);
      return;
    }
    state.at = next;
    state.typed = 0;                       // never coalesce onto a jump
    state.applying = true;
    area.value = state.stack[next];
    // What actually tells Gradio. Its binding listens for `input`, and a
    // value assigned from script fires nothing on its own — without this
    // the box would show the old text while Generate still sent the new.
    area.dispatchEvent(new Event("input", { bubbles: true }));
    state.applying = false;
    undoRefresh(state);
  }

  function undoAttach(area) {
    if (area.dataset.kxUndo || area.disabled || area.readOnly) return;
    area.dataset.kxUndo = "1";
    const state = undoState(area);
    const bar = document.createElement("div");
    bar.className = "kx-undo";
    for (const [name, delta] of [["Undo", -1], ["Redo", 1]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "kx-undo-btn";
      button.title = name + (name === "Undo" ? " (Ctrl+Z)" : " (Ctrl+Y)");
      button.setAttribute("aria-label", name);
      button.innerHTML = UNDO_ICONS[name];
      // Pressing a button takes focus, which on a phone shuts the keyboard
      // between taps. Refusing the focus keeps the caret in the box, so
      // undo can be tapped several times in a row.
      button.addEventListener("pointerdown", (event) => event.preventDefault());
      button.addEventListener("click", () => undoStep(area, delta));
      bar.appendChild(button);
      state.buttons[name] = button;
    }
    undoRefresh(state);
    // Inside the component's own block, so it travels with the box it
    // belongs to rather than sitting loose in the column.
    (area.closest(".block") || area.parentElement).appendChild(bar);
  }

  function undoScan() {
    document.querySelectorAll("textarea:not([data-kx-undo])")
            .forEach(undoAttach);
  }

  document.addEventListener("input", (event) => {
    const area = event.target;
    if (!(area instanceof HTMLTextAreaElement) || !area.dataset.kxUndo) return;
    const state = undoStates.get(area);
    if (!state || state.applying) return;
    // One entry per burst, the way an editor does it: a pause, or the end
    // of a word, closes the current entry and opens the next. Without it
    // undo would step back one character at a time, which on a phone is
    // worse than no undo at all.
    const now = Date.now();
    if (now - state.typed > UNDO_COALESCE_MS) {
      state.stack.length = state.at + 1;
      state.stack.push(area.value);
      if (state.stack.length > UNDO_DEPTH) state.stack.shift();
      state.at = state.stack.length - 1;
    } else {
      state.stack[state.at] = area.value;
    }
    state.typed = /\s$/.test(area.value) ? 0 : now;
    undoRefresh(state);
  });

  document.addEventListener("keydown", (event) => {
    const area = event.target;
    if (!(area instanceof HTMLTextAreaElement) || !area.dataset.kxUndo) return;
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
    const key = (event.key || "").toLowerCase();
    let delta = 0;
    if (key === "z") delta = event.shiftKey ? 1 : -1;
    else if (key === "y") delta = 1;
    else return;
    event.preventDefault();
    undoStep(area, delta);
  });

  undoScan();
  // A box can appear long after load — a licence decides which tabs are
  // built, and the pricing panel swaps whole sections in and out. focusin
  // catches anything the observer somehow missed, at the moment it starts
  // to matter.
  document.addEventListener("focusin", (event) => {
    if (event.target instanceof HTMLTextAreaElement) undoAttach(event.target);
  });
  let undoPending = false;
  new MutationObserver(() => {
    if (undoPending) return;      // the queue's poll touches the DOM every
    undoPending = true;           // second; one scan a frame is plenty
    requestAnimationFrame(() => { undoPending = false; undoScan(); });
  }).observe(document.documentElement, { childList: true, subtree: true });
})();
"""


# --------------------------------------------------------------------------
# Header / footer markup
# --------------------------------------------------------------------------
def _escape(text) -> str:
    """Minimal HTML escape — these strings are paths and counts, not prose."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _plan_pill(plan_name) -> str:
    """The tier this license sits on, or what to say when it names none."""
    if not plan_name:
        # A license can list its features directly instead of naming a
        # tier — an ordinary case, and one worth naming rather than leaving
        # the bar looking as though the plan failed to load.
        return ('<span class="kx-pill" title="This licence grants its tabs '
                'directly rather than through a plan">Custom licence</span>')
    label = str(plan_name).strip()
    if not label.lower().endswith(("plan", "tier")):
        label += " plan"
    return ('<span class="kx-pill kx-pill-accent" title="The plan this '
            f'licence is on">{_escape(label)}</span>')


def _expiry_pill(expires_at) -> str:
    """"Expires 12 Aug 2026", amber inside the last week, or "" for never.

    `expires_at` is a UTC datetime from licensing.expires_at() and is None
    for a key with no end date, which renders nothing at all — a bar that
    says "Expires never" is noise.
    """
    if expires_at is None:
        return ""

    when = f"{expires_at.day} {expires_at:%b %Y}"
    days = (expires_at - datetime.now(timezone.utc)).days
    if days < 0:
        # Barely reachable: the server refuses to start an expired license
        # and the heartbeat stops a running one within the minute. It is
        # here so a skewed clock reads as a warning and not as a date in
        # the future.
        return ('<span class="kx-pill kx-pill-warn" title="The licence '
                f'server will refuse this key">Expired {when}</span>')
    left = {0: "today", 1: "tomorrow"}.get(days, f"in {days} days")
    soon = " kx-pill-warn" if days <= 7 else ""
    return (f'<span class="kx-pill{soon}" title="This licence expires '
            f'{left}">Expires {when}</span>')


# The Ember flame, drawn at whatever size its container gives it and in
# whatever colour the container sets (see the .kx-logo svg rule). Same
# decimated outline as _FAVICON and for the same reason — it renders at
# 22px here. assets/branding/ember-logo.svg holds the full-fidelity curve.
_MARK = (
    "<svg viewBox='0 0 396 572' xmlns='http://www.w3.org/2000/svg' "
    "aria-hidden='true' focusable='false'><path fill='currentColor' d='"
    "M208 5.9L175.6 37.8L168.5 48.3L157.7 69.4L151.4 86L151.4 "
    "89.5L149.9 90.4L147.1 116.9L147.6 137.5L151.1 158.5L159.8 "
    "185.7L169.8 203.3L194.8 238.1L228.2 277.7L233.6 286.8L202.9 "
    "264.8L168.5 230.2L146.4 205L138.6 192.9L129 172.2L121.2 "
    "135.1L110.9 144.1L100.6 157.3L85.4 191.5L82.2 216.3L83.3 "
    "236.2L89.1 258.6L101.1 285.7L111.2 300.8L130 324.1L130.8 "
    "327.4L137 333.2L140.9 339.8L142.7 340L155.6 357.3L149.8 "
    "357.9L126.8 348.6L106.8 335L90.5 318.8L78.8 304.3L79.6 "
    "303.7L71.7 290L64.5 271.5L59.7 244.4L57.5 243.3L51.8 248L26.7 "
    "279.1L21.5 291.7L19.9 292.4L16.1 300.2L4.9 338.4L0.2 370.9L2.6 "
    "407.2L6.9 427.3L13.2 446.2L19.8 461.4L31 480.8L58.9 515.9L92.9 "
    "541.4L113.7 553.1L134.6 561.7L180.2 571.6L223.3 570.2L259.6 "
    "561.7L299.7 542.4L329 520.6L343.7 506.7L360.3 486.4L376.5 "
    "459.5L388.5 427.5L395.4 385.7L395.6 354.7L391.6 329.1L384.4 "
    "301.8L373.8 276.9L351.9 239.4L314.1 195.7L312.5 192.2L305.9 "
    "186.7L281 155.6L278.8 154.6L274.5 147.2L255.8 125.4L239 "
    "100.5L227.2 77.5L219.4 54L216.7 32.8L216.8 0.3L208 5.9Z"
    "M139 478.6L150.8 485.8L160.7 494.6L169.3 505.6L177.1 519.9L183.3 "
    "542.6L183.4 559.9L181.4 569.3L180.1 567.1L171.8 562.4L157.6 "
    "548.8L143.4 526.6L136.4 507.4L132.9 485.4L132.9 476L139 478.6Z"
    "'/></svg>"
)


def header_html(plan_name=None, expires_at=None) -> str:
    """The application bar: the brand, and the state of this license.

    Deliberately short on facts. The engine chips, the model and GPU counts
    and the output path all used to live up here, and each was true but
    ambient — a bar that carries every fact carries none of them. What is
    left is the brand plus the two things a customer cannot read off the
    page itself: which plan they are on and when it runs out. The counts
    and the path moved to the footer (see footer_html), and the engine
    chips are gone for good, because the tab strip immediately below is a
    better list of the engines than a row of pills naming the same ones.

    Both arguments come from licensing and are both routinely None — a key
    can name no plan and can have no expiry — so neither is required and
    neither renders a placeholder.

    The way into the pricing panel sits next to this markup as a real
    Gradio button, not as a link in here: that panel is a view of this same
    page, so there is no URL for an <a> to point at. See ui.py.
    """
    return f"""
<div class="kx-bar">
  <div class="kx-brand">
    <div class="kx-logo">{_MARK}</div>
    <div>
      <h1 class="kx-name">Ember</h1>
      <p class="kx-tagline">ComfyUI generation suite · RunPod</p>
    </div>
  </div>
  <div class="kx-stats">
    {_plan_pill(plan_name)}
    {_expiry_pill(expires_at)}
  </div>
</div>
"""


def footer_html(model_count: int, gpu_count: int, output_dir) -> str:
    """The line under the tabs: the keyboard hint and the ambient facts.

    The counts and the output path were in the header until it was cut back
    to the license (see header_html). They are worth keeping — the path in
    particular gets typed into scp/rsync often enough to be worth a click
    to copy — but they are reference material rather than something read on
    every glance, so they live down here next to the other thing you look
    up once.
    """
    path = _escape(output_dir)
    return f"""
<div class="kx-bar">
  <div class="kx-hint">
    <kbd>Ctrl</kbd><span>+</span><kbd>Enter</kbd>
    <span>runs the tab you are on</span>
  </div>
  <div class="kx-stats">
    <span class="kx-pill"><b>{model_count}</b> model{'' if model_count == 1 else 's'}</span>
    <span class="kx-pill"><b>{gpu_count}</b> GPU{'' if gpu_count == 1 else 's'}</span>
    <span class="kx-pill kx-path" data-kx-copy="{path}"
          title="Click to copy the output directory">{path}</span>
  </div>
</div>
"""


# --------------------------------------------------------------------------
# Pricing page markup
# --------------------------------------------------------------------------
# Rendered as one HTML block rather than built from Gradio components: it is
# a read-only card grid with nothing to submit, and a CSS grid lays that out
# in a way a nest of gr.Row/gr.Column does not. Everything it needs arrives
# as an argument, so this file still imports nothing but gradio and knows
# nothing about how the catalogue was fetched.

# Symbols for the currencies the catalogue is likely to quote. Anything else
# falls back to the code itself ("42 CHF") — correct if less pretty, and
# better than guessing a symbol for a currency we do not know.
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def _price(amount, currency: str) -> str:
    """`19` + `USD` → `$19`. Whole numbers lose the trailing `.0`."""
    if amount is None:
        return ""
    symbol = _SYMBOLS.get(currency.upper(), "")
    figure = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"{symbol}{figure}" if symbol else f"{figure} {currency.upper()}"


# ------------------------------------------------------------ billing cycles
# The tabs over the card grid. Everything about them — how many there are,
# what they are called, what they save — arrives in the catalogue, so
# launching quarterly or yearly pricing is a flag on the licence server and
# nothing here has to be rebuilt for it. With one cycle there is no tab bar
# and the page reads exactly as it did before cycles existed.

# The shape this file reads off a plans.Cycle, and the fallback term for a
# catalogue that offers none — a namedtuple rather than the real class
# because this module still imports nothing but gradio and the stdlib.
_Cycle = namedtuple("_Cycle", "id label months discount_percent")
_MONTHLY = _Cycle("monthly", "Monthly", 1, 0.0)


# The radio input id for a cycle. Also the CSS hook the generated rules key
# on, so it is built in one place rather than spelled out at each use.
def _cycle_id(cycle) -> str:
    return f"kx-cycle-{cycle.id}"


# What a cycle id may contain. See _usable_cycles.
_CYCLE_ID_OK = re.compile(r"[a-z0-9_-]{1,32}")


def _usable_cycles(catalogue) -> list:
    """The cycles that can safely be rendered, in the order they arrived.

    A cycle id becomes part of a DOM id *and* part of a CSS selector in the
    generated stylesheet, so anything outside `[a-z0-9_-]` is dropped rather
    than escaped: the ids are a short closed vocabulary set by the server
    ("monthly", "quarterly", "yearly"), and refusing the ones that are not
    is both simpler and the reason a hand-edited billing document cannot
    inject a selector — or anything else — into this page.
    """
    return [
        cycle for cycle in getattr(catalogue, "cycles", ()) or ()
        if cycle.id and _CYCLE_ID_OK.fullmatch(cycle.id)
    ]


def _cycle_style(cycles: list) -> str:
    """The rules that make the tabs work, for these cycles specifically.

    Generated rather than written into the stylesheet because the cycle ids
    come from the database and CSS cannot match on an attribute it has not
    been told about. Three things per cycle: light its tab (with the badge
    inside it recoloured for the accent fill it now sits on), reveal its
    price block, and mark it while it holds keyboard focus.

    The first cycle is the one checked on load and the one the stylesheet
    shows by default, so it needs no reveal rule — only the swap *away*
    from it that every other cycle carries.
    """
    if len(cycles) < 2:
        return ""

    rules = []
    for index, cycle in enumerate(cycles):
        node = _cycle_id(cycle)
        tab = f'#{node}:checked ~ .kx-cycle-tabs label[for="{node}"]'
        rules.append(f"""
{tab} {{
  background: var(--kx-tab-on-bg);
  color: var(--kx-tab-on-fg) !important;
  box-shadow: var(--kx-tab-on-shadow);
}}
{tab} .kx-cycle-save {{
  background: rgba(255, 255, 255, .22);
  color: var(--kx-tab-on-fg) !important;
}}
#{node}:focus-visible ~ .kx-cycle-tabs label[for="{node}"] {{
  outline: 2px solid var(--kx-accent);
  outline-offset: 2px;
}}""")
        if index:
            rules.append(f"""
#{node}:checked ~ .kx-plan-grid .kx-price-first {{ display: none; }}
#{node}:checked ~ .kx-plan-grid .kx-price-{cycle.id} {{ display: block; }}""")
    return "<style>" + "\n".join(rules) + "\n</style>"


def _cycle_tabs(cycles: list) -> str:
    """The radios and the segmented control they drive.

    Returns "" for a single cycle: one tab is not a choice, and a control
    that cannot be changed is furniture. The radios must stay in the same
    parent as, and before, both the tab bar and the card grid — every rule
    in _cycle_style is a sibling selector.
    """
    if len(cycles) < 2:
        return ""
    inputs = "".join(
        f'<input type="radio" name="kx-cycle" class="kx-cycle-radio" '
        f'id="{_cycle_id(cycle)}"{" checked" if index == 0 else ""}>'
        for index, cycle in enumerate(cycles)
    )
    labels = "".join(
        f'<label for="{_cycle_id(cycle)}">{_escape(cycle.label)}'
        + (f'<span class="kx-cycle-save">Save '
           f'{_percent(cycle.discount_percent)}%</span>'
           if cycle.discount_percent > 0 else "")
        + "</label>"
        for cycle in cycles
    )
    return f'{inputs}<div class="kx-cycle-tabs">{labels}</div>'


def _percent(value) -> str:
    """`20.0` → `20`. Discounts are quoted as round numbers where they are."""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


# The term a price is quoted per — "/month", "/quarter", "/year". Taken
# from the length of the cycle rather than its label, so a cycle renamed on
# the server ("Annual", "Yearly — best value") still reads correctly here,
# and one of an unusual length gets an honest "/18 months" instead of a
# noun invented for it.
_TERMS = {1: "month", 3: "quarter", 6: "6 months", 12: "year"}


def _term(months: int) -> str:
    return _TERMS.get(months, f"{months} months")


def _cycle_price(plan, cycle, is_first: bool) -> str:
    """One card's price for one cycle: the headline and what it works out at.

    The headline is what the customer is actually charged for the term —
    ₹14,390/year, not ₹1,199/month with the year in the small print. The
    line under it carries the two things that headline hides: the monthly
    equivalent, which is the only figure comparable across the tabs, and
    the saving, which is the argument for being on this tab at all.

    The monthly equivalent is rounded to a whole unit. It is a comparison,
    the exact sum is the figure above it, and ₹1,199.17 is a worse answer
    to "so how much a month" than ₹1,199.
    """
    classes = (f"kx-price kx-price-{cycle.id}"
               + (" kx-price-first" if is_first else ""))
    price = (getattr(plan, "prices", None) or {}).get(cycle.id)
    if price is None:
        # A tier with no price at all. Still gets a block per cycle so the
        # cards stay the same height whichever tab is showing.
        return (f'<div class="{classes}">'
                '<p class="kx-plan-price kx-plan-poa">Price on application</p>'
                "</div>")

    total = _escape(_price(price.total, plan.currency))
    if price.months == 1:
        note = f"Billed {_escape(cycle.label.lower())}"
    else:
        monthly = _escape(_price(round(price.per_month), plan.currency))
        note = f"{monthly}/month, billed {_escape(cycle.label.lower())}"
    if price.saving > 0:
        note += ('<span class="kx-plan-saving"> · save '
                 f"{_escape(_price(price.saving, plan.currency))}</span>")

    return f"""<div class="{classes}">
    <p class="kx-plan-price"><b>{total}</b><span>/{_term(price.months)}</span></p>
    <p class="kx-plan-billed">{note}</p>
  </div>"""


# The id the contact dialog is opened by. One dialog for the whole page —
# every card asks the same question — so the buttons all point here.
_CONTACT_ID = "kx-contact"

# The plan grid, so the showcase further down the page can link back up to
# it. Named here rather than written twice because the two are in different
# functions and a typo would be a link that silently goes nowhere.
_PLANS_ID = "kx-plans"


def _plan_cta(plan, is_current: bool) -> str:
    """The button at the foot of a card.

    Three states, and only one of them is a link. The tier already held
    gets a flat non-interactive slab: there is nothing to ask for, and a
    live button that reopened a "contact us" dialog would be inviting the
    customer to buy what they have. The rest open the dialog, with the
    recommended tier's filled so the eye lands there first.
    """
    if is_current:
        return ('<div class="kx-plan-cta"><span class="kx-plan-btn '
                'kx-plan-btn-current">Current plan</span></div>')
    primary = " kx-plan-btn-primary" if plan.is_popular else ""
    return (
        f'<div class="kx-plan-cta">'
        f'<a class="kx-plan-btn{primary}" href="#{_CONTACT_ID}">Get Started</a>'
        f"</div>"
    )


def _contact_modal(contact_url: str | None) -> str:
    """The "talk to us" dialog every Get Started button opens.

    Rendered once per page and hidden until its id is the URL fragment —
    see .kx-modal in the stylesheet for why it is CSS-only.

    With no CONTACT_URL set the dialog still opens and still says what to
    do; it just has no button to offer. That is the honest failure: a link
    to nowhere would be worse than prose, and this is the state the page
    ships in until the Telegram URL is configured.
    """
    if contact_url:
        action = (f'<a class="kx-plan-btn kx-plan-btn-primary" '
                  f'href="{_escape(contact_url)}" target="_blank" '
                  f'rel="noopener noreferrer">Message us on Telegram</a>')
    else:
        action = ""
    return f"""
<div class="kx-modal" id="{_CONTACT_ID}">
  <a class="kx-modal-scrim" href="#" aria-label="Close"></a>
  <div class="kx-modal-card" role="dialog" aria-modal="true"
       aria-labelledby="{_CONTACT_ID}-title">
    <h3 id="{_CONTACT_ID}-title">Contact the admin</h3>
    <p>Plans are issued by hand against your licence key. Get in touch and
       we will move you over — your pod keeps running in the meantime.</p>
    <div class="kx-modal-actions">
      {action}
      <a class="kx-modal-close" href="#">Close</a>
    </div>
  </div>
</div>
"""


def _plan_card(plan, catalogue, cycles: list, is_current: bool) -> str:
    """One tier: what it is called, what it costs, and every tab it grants.

    The feature list is the point of the page — "the services they get" —
    so each row carries the registry's own name *and* its description
    rather than a bare key, and the plan's order is kept as the server
    sorted it.

    `cycles` is every billing term on offer, and the card carries a price
    block for each; the tabs above the grid decide which one is on screen.
    """
    rows = "".join(
        f'<li><span class="kx-tick" aria-hidden="true">✓</span>'
        f'<span class="kx-feat"><b>{_escape(info.name)}</b>'
        + (f"<i>{_escape(info.description)}</i>" if info.description else "")
        + "</span></li>"
        for info in (catalogue.describe(key) for key in plan.features)
    )
    if not rows:
        # A plan that grants nothing is a real (if odd) document — say so
        # rather than render an empty card that looks like a load failure.
        rows = ('<li class="kx-plan-empty">No tabs are included on this '
                "plan.</li>")

    badge = ('<span class="kx-plan-badge">Your plan</span>'
             if is_current else "")
    prices = "".join(
        _cycle_price(plan, cycle, is_first=index == 0)
        for index, cycle in enumerate(cycles)
    )
    description = (f'<p class="kx-plan-desc">{_escape(plan.description)}</p>'
                   if plan.description else "")

    classes = "kx-plan"
    if plan.is_popular:
        classes += " kx-plan-popular"
    if is_current:
        classes += " kx-plan-current"
    flag = ('<span class="kx-plan-flag">Most Popular</span>'
            if plan.is_popular else "")

    return f"""
<article class="{classes}">
  {flag}
  <header class="kx-plan-head">
    <h3>{_escape(plan.name)}</h3>
    {badge}
  </header>
  {description}
  {prices}
  <ul class="kx-plan-feats">{rows}</ul>
  {_plan_cta(plan, is_current)}
</article>
"""


def pricing_html(catalogue, current_plan_id=None,
                 current_plan_name=None) -> str:
    """The Pricing page body.

    `catalogue` is a plans.Catalogue — taken duck-typed rather than
    imported so this module keeps depending on nothing but gradio. It is
    read for `.plans`, `.cycles`, `.error` and `.describe(key)`.

    `current_plan_id` / `current_plan_name` come from licensing.plan() and
    are both None whenever the licence names no plan or the server did not
    say. That is an ordinary case, not an error: the page then simply
    marks nothing, which is why neither is required to render.
    """
    if catalogue.error:
        # .kx-note-warn, not -error: the plans could not be read, which
        # says nothing about whether this pod is licensed. It is running,
        # so it plainly is.
        return f"""
<div class="kx-pricing">
  <div class="kx-note kx-note-warn">
    <p><strong>The plan list is not available right now.</strong>
       {_escape(catalogue.error)}</p>
    <p>This does not affect the tabs you already have — they come from
       your licence key, which was checked when this pod started.</p>
  </div>
</div>
"""

    # A catalogue with no usable cycle at all still has to render: fall back
    # to a single monthly term, which is what every price on it means when
    # nothing says otherwise.
    cycles = _usable_cycles(catalogue) or [_MONTHLY]
    cards = "".join(
        _plan_card(plan, catalogue, cycles,
                   is_current=plan.id == current_plan_id)
        for plan in catalogue.plans
    )
    if current_plan_name:
        standing = (f"You are on <strong>{_escape(current_plan_name)}</strong>."
                    " The tabs above the fold are the ones it grants.")
    else:
        # Either the licence lists its features directly (a one-off deal
        # that fits no tier) or the server did not send a plan. Both mean
        # the same thing to a reader: match the tabs you have to the list.
        standing = ("Your licence grants its tabs directly rather than "
                    "through a plan, so none is marked as yours below.")

    return f"""
<div class="kx-pricing">
  <div class="kx-cycle-scope">
    {_cycle_tabs(cycles)}
    <div class="kx-plan-grid" id="{_PLANS_ID}">{cards}</div>
  </div>
</div>
{_cycle_style(cycles)}
{_contact_modal(getattr(catalogue, "contact_url", None))}
"""


# -------------------------------------------------------- feature showcase
# The section under the plan cards: what each granted tab does, in prose and
# pictures. showcase.py decides what is in it and where the files are; every
# function here only turns that into markup, and is duck-typed on it for the
# same reason pricing_html is — this module still imports nothing but gradio
# and the stdlib.
#
# Two shapes recur and are worth stating once:
#
#   * `boxes` is threaded through every renderer. A picture is zoomable by
#     being an <a> pointing at a dialog, and that dialog has to be emitted
#     somewhere that is not inside the block — so each _shot appends its own
#     to this list and showcase_html prints them at the end. The id is the
#     list's length at the time, which is unique by construction.
#   * a block is rendered by a function of the same name, and the two
#     container blocks call back into _showcase_block, so nesting a compare
#     inside a split needs nothing special.

# What a placeholder shows in the middle. Only two, because the tile's job is
# to name the file it wants, not to illustrate it.
_PH_IMAGE = "🖼️"
_PH_VIDEO = "🎬"


def _media_tag(media) -> str:
    """The <img>/<video> for a picture the bucket is expected to hold.

    Lazy and async everywhere: the whole section sits in a panel that starts
    hidden, so nothing is fetched until the customer opens Plans & pricing
    and scrolls to it — which is what lets a page of forty screenshots cost
    nothing to put in the DOM. Videos are muted and looping because they are
    used as animated stills; a clip with sound that starts itself is not
    what anyone wants from a page of screenshots.

    `alt` is empty on purpose. Every picture is drawn over the placeholder
    tile that names it, so a file that has not been uploaded yet already
    says what it is — and alt text would print itself over that tile in the
    one state it is there to handle.
    """
    url = _escape(media.url)
    if media.is_video:
        return (f'<video src="{url}" autoplay muted loop playsinline '
                f'preload="none"></video>')
    return f'<img src="{url}" alt="" loading="lazy" decoding="async">'


def _placeholder(media) -> str:
    """The tile that names the file a slot expects.

    Rendered under *every* picture, not only the ones with no URL. The
    pictures come from a bucket this app never talks to, so nothing here
    knows whether one has actually been uploaded — and a tile underneath
    costs one span, while the alternative is a broken-image icon on the
    page a customer is being asked to buy from. When the picture loads it
    covers this completely; when it does not, the reader sees a designed
    tile and whoever is filling the bucket sees the exact path that is
    missing.
    """
    glyph = _PH_VIDEO if media.is_video else _PH_IMAGE
    return (f'<span class="kx-ph" style="--kx-h: {int(media.hue)}">'
            f'<span class="kx-ph-glyph" aria-hidden="true">{glyph}</span>'
            f'<span class="kx-ph-path">{_escape(media.path)}</span></span>')


def _lightbox(box_id: str, media) -> str:
    """The zoom view for one picture."""
    if media.is_video:
        inner = (f'<video src="{_escape(media.url)}" controls autoplay muted '
                 f'loop playsinline preload="none"></video>')
    else:
        inner = (f'<img src="{_escape(media.url)}" loading="lazy" '
                 f'alt="{_escape(media.caption or media.path)}">')
    caption = media.caption or media.label
    return f"""
<div class="kx-lb" id="{box_id}">
  <a class="kx-lb-scrim" href="#" aria-label="Close"></a>
  <div class="kx-lb-card" role="dialog" aria-modal="true">
    <a class="kx-lb-close" href="#">✕ Close</a>
    {inner}
    {f'<p class="kx-lb-cap">{_escape(caption)}</p>' if caption else ""}
  </div>
</div>"""


def _shot(media, boxes: list, classes: str = "") -> str:
    """One picture in its frame, zoomable if there is anything to zoom.

    A missing one is a <figure> rather than an <a>: a placeholder has no
    larger version, and a link that opened an empty dialog would be worse
    than no link.
    """
    tag = "figure"
    attrs = ""
    shape = f' style="aspect-ratio: {media.ratio}"' if media.ratio else ""
    frame = f"kx-shot {classes}".strip()
    if media.ratio:
        frame += " kx-shot-fixed"

    # The tile always goes in first and the picture, if there is one, is
    # laid over it — see _placeholder for why that is the default rather
    # than the fallback.
    inner = _placeholder(media)
    if not media.missing:
        inner += _media_tag(media)
        box_id = f"kx-lb-{len(boxes)}"
        boxes.append(_lightbox(box_id, media))
        tag, attrs = "a", f' href="#{box_id}"'

    label = ""
    if media.label:
        accent = " kx-tag-accent" if media.accent else ""
        label = f'<span class="kx-tag{accent}">{_escape(media.label)}</span>'
    caption = ""
    if media.caption and not media.missing:
        caption = f'<span class="kx-shot-cap">{_escape(media.caption)}</span>'

    return (f'<{tag} class="{frame}"{attrs}{shape}>'
            f"{label}{inner}{caption}</{tag}>")


def _block_caption(block) -> str:
    """The line under a block. The first sentence is emphasised."""
    if not block.caption:
        return ""
    head, sep, tail = block.caption.partition(". ")
    if sep and tail:
        text = f"<b>{_escape(head)}.</b> {_escape(tail)}"
    else:
        text = _escape(block.caption)
    return f'<p class="kx-block-cap">{text}</p>'


def _columns(count: int) -> str:
    """The grid class for a row of `count` pictures, within reason."""
    return f"kx-cols-{min(max(count, 2), 5)}"


def _hero(block, boxes: list) -> str:
    """One wide plate with the prompt sitting on it."""
    media = block.items[0]
    text = ""
    if block.title or block.note:
        title = f"<b>{_escape(block.title)}</b>" if block.title else ""
        note = f"<span>{_escape(block.note)}</span>" if block.note else ""
        text = f'<div class="kx-hero-text">{title}{note}</div>'
    return f'<div class="kx-hero">{_shot(media, boxes)}{text}</div>'


def _shot_block(block, boxes: list) -> str:
    return _shot(block.items[0], boxes)


def _compare(block, boxes: list) -> str:
    """Before → after, with the arrow that makes it read as a sequence."""
    before, after = block.items[0], block.items[1]
    return (f'<div class="kx-compare">{_shot(before, boxes)}'
            f'<div class="kx-arrow" aria-hidden="true">→</div>'
            f"{_shot(after, boxes)}</div>")


def _fan(block, boxes: list) -> str:
    """One source on the left, everything it turned into on the right."""
    outputs = "".join(_shot(item, boxes) for item in block.items)
    return f"""<div class="kx-fan">
  <div class="kx-fan-src">{_shot(block.source, boxes)}</div>
  <div class="kx-arrow" aria-hidden="true">→</div>
  <div class="kx-fan-out {_columns(len(block.items))}">{outputs}</div>
</div>"""


def _row(block, boxes: list) -> str:
    shots = "".join(_shot(item, boxes) for item in block.items)
    return f'<div class="kx-row {_columns(len(block.items))}">{shots}</div>'


def _strip(block, boxes: list) -> str:
    shots = "".join(_shot(item, boxes) for item in block.items)
    note = (f'<p class="kx-strip-note">↔ scroll · {_escape(block.note)}</p>'
            if block.note else "")
    return f'<div class="kx-strip">{shots}</div>{note}'


def _collage(block, boxes: list) -> str:
    shots = "".join(_shot(item, boxes) for item in block.items)
    flat = " kx-collage-flat" if block.flat else ""
    return f'<div class="kx-collage{flat}">{shots}</div>'


def _split(block, boxes: list) -> str:
    """Prose beside a block instead of above it."""
    title = f"<h4>{_escape(block.title)}</h4>" if block.title else ""
    body = f"<p>{_escape(block.body)}</p>" if block.body else ""
    reverse = " kx-split-reverse" if block.reverse else ""
    inner = "".join(_showcase_block(item, boxes) for item in block.blocks)
    return (f'<div class="kx-split{reverse}"><div>{title}{body}</div>'
            f"<div>{inner}</div></div>")


def _pair(block, boxes: list) -> str:
    """Two blocks side by side, each keeping its own caption."""
    inner = "".join(f"<div>{_showcase_block(item, boxes)}</div>"
                    for item in block.blocks)
    return f'<div class="kx-pair">{inner}</div>'


_BLOCKS = {
    "hero": _hero,
    "shot": _shot_block,
    "compare": _compare,
    "fan": _fan,
    "row": _row,
    "strip": _strip,
    "collage": _collage,
    "split": _split,
    "pair": _pair,
}


def _showcase_block(block, boxes: list) -> str:
    """One block of any kind, plus its caption.

    An unknown type renders as nothing. showcase.py already refuses to build
    one, so this is the second half of the same rule rather than a check that
    is expected to fire.
    """
    render = _BLOCKS.get(block.type)
    if render is None:
        return ""
    body = render(block, boxes)
    if block.type in ("split", "pair"):
        # Their captions belong to the blocks they carry, which have already
        # printed them.
        return f'<div class="kx-block">{body}</div>'
    return f'<div class="kx-block">{body}{_block_caption(block)}</div>'


def _showcase_section(section, number: int, boxes: list) -> str:
    """One feature: what it is called, what it does, what it produces."""
    if section.locked:
        chip = '<span class="kx-chip-off">🔓 Not in your plan</span>'
        # Points at the plan grid this page opens with rather than at the
        # contact dialog: the next question after "I want this" is "which
        # tier has it", and that is answered up there.
        upgrade = f"""
  <div class="kx-feat-up">
    <span>Not included on your current plan — the plans above show which
          one adds it.</span>
    <a href="#{_PLANS_ID}">See the plans ↑</a>
  </div>"""
    else:
        chip = '<span class="kx-chip-on">✓ In your plan</span>'
        upgrade = ""
    body = "".join(f"<p>{_escape(para)}</p>" for para in section.body)
    highlights = ""
    if section.highlights:
        chips = "".join(f"<span>{_escape(item)}</span>"
                        for item in section.highlights)
        highlights = f'<div class="kx-hl">{chips}</div>'
    blocks = "".join(_showcase_block(block, boxes) for block in section.blocks)

    return f"""
<article class="kx-feat" id="kx-feat-{_escape(section.key)}">
  <header class="kx-feat-head">
    <div class="kx-feat-no" aria-hidden="true">{number:02d}</div>
    <div class="kx-feat-eyebrow">
      <span class="kx-feat-tab">{_escape(section.label)}</span>
      {chip}
    </div>
    <h3>{_escape(section.headline)}</h3>
    <div class="kx-feat-body">{body}{highlights}</div>
  </header>
  {blocks}{upgrade}
</article>"""


def showcase_html(showcase) -> str:
    """The whole section under the plan cards, or "" if there is none.

    `showcase` is a showcase.Showcase, or None on a build whose assets were
    not bundled — in which case the pricing page is exactly what it was
    before this section existed, which is the right way for a decorative
    panel to fail.
    """
    if showcase is None or not showcase.sections:
        return ""

    boxes: list = []
    sections = "".join(
        _showcase_section(section, index + 1, boxes)
        for index, section in enumerate(showcase.sections)
    )

    title = _escape(showcase.title)
    if showcase.title_accent:
        title = f"{title} <em>{_escape(showcase.title_accent)}</em>"
    intro = "".join(f"<p>{_escape(para)}</p>" for para in showcase.body)

    stats = ""
    if showcase.stats:
        cells = "".join(
            f'<div class="kx-show-stat"><b>{_escape(stat.value)}</b>'
            f"<span>{_escape(stat.label)}</span></div>"
            for stat in showcase.stats
        )
        stats = f'<div class="kx-show-stats">{cells}</div>'

    # One chip per section, in the order the tabs are in. Only worth the
    # space once there are enough sections to scroll past.
    jump = ""
    if len(showcase.sections) > 2:
        links = "".join(
            f'<a href="#kx-feat-{_escape(section.key)}">'
            f"{_escape(section.label)}</a>"
            for section in showcase.sections
        )
        jump = f'<nav class="kx-jump">{links}</nav>'

    cta = ""
    if showcase.cta_title:
        body = (f"<p>{_escape(showcase.cta_body)}</p>"
                if showcase.cta_body else "")
        # Points at the same dialog the plan cards' buttons open, which is
        # rendered once by pricing_html further up the page.
        cta = f"""
<div class="kx-show-cta">
  <h3>{_escape(showcase.cta_title)}</h3>
  {body}
  <a class="kx-plan-btn kx-plan-btn-primary" href="#{_CONTACT_ID}">Talk to us</a>
</div>"""

    return f"""
<section class="kx-pricing kx-show">
  <div class="kx-show-seam"><span>{_escape(showcase.eyebrow)}</span></div>
  <div class="kx-show-intro">
    <h2>{title}</h2>
    {intro}
    {stats}
  </div>
  {jump}
  {sections}
  {cta}
</section>
{"".join(boxes)}
"""


def launch_kwargs() -> dict:
    """The look-and-feel arguments for `Blocks.launch()`.

    One place for them because two callers launch the same UI: ui.launch_ui()
    on a pod and scripts/dryrun.py locally. Anything that only affects how
    the app *looks* belongs here; ports, sharing and allowed paths stay with
    the caller, which is what actually differs between the two.
    """
    return {"theme": THEME, "css": CSS, "head": HEAD, "js": JS}
