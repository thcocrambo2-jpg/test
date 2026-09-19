import type { TabCategory } from '@/api/types'

/*
 * The navigation, grouped.
 *
 * The Gradio app had seven flat emoji tabs in one row — Krea2, Krea2 V2,
 * Krea2 Edit, V2 Edit, Wan Video, Prompt Library, Gallery. Nothing about a
 * flat row tells you that two of them make pictures from nothing and two of
 * them change a picture you already have.
 *
 * The categories are not invented here: the licence server already stores
 * `category` per feature (generation / editing / video / tools) in the
 * features collection, and it already crosses the wire on the acquire
 * response. `_clean_feature_info` (licensing.py:200) currently throws it away.
 * Carrying it through is the four-line change in Section 2 — see
 * context.md §4.9. Until then the mapping lives on each tab's schema.
 */

export const CATEGORY_LABEL: Record<TabCategory, string> = {
  generate: 'Generate',
  edit: 'Edit',
  video: 'Video',
  library: 'Library',
}

export const CATEGORY_ORDER: TabCategory[] = ['generate', 'edit', 'video', 'library']

/** Tabs with no schema-driven form — bespoke pages. Gallery and Prompt Library
 *  are the only two of the nine that are genuinely their own thing. */
export interface BespokeNavItem {
  key: string
  label: string
  icon: string
  category: TabCategory
  route: string
  ready: boolean
}

export const BESPOKE_TABS: BespokeNavItem[] = [
  {
    key: 'gallery',
    label: 'Gallery',
    icon: '\u{1F5BC}️',
    category: 'library',
    route: '/library/gallery',
    ready: true,
  },
  {
    key: 'community_prompts',
    label: 'Prompt Library',
    icon: '\u{1F31F}',
    category: 'library',
    route: '/library/prompts',
    ready: false,
  },
]
