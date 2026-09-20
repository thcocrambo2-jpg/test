import type { TabCategory } from '@/api/types'

/*
 * The navigation, grouped.
 *
 * Four groups, because nothing about a flat row of tabs tells you which of
 * them make a picture from nothing and which change one you already have.
 *
 * The grouping is a constant on each tab's schema, not data from the licence
 * server. The server does store a `category` per feature (generation /
 * editing / video / tools) and sends it on the acquire response, but the pod
 * discards it: `seat._clean_feature_info` keeps only the tab labels. See "Tab
 * order and routing" in `docs/architecture/web-ui.md`.
 */

export const CATEGORY_LABEL: Record<TabCategory, string> = {
  generate: 'Generate',
  edit: 'Edit',
  video: 'Video',
  library: 'Library',
}

export const CATEGORY_ORDER: TabCategory[] = ['generate', 'edit', 'video', 'library']

/** Tabs with no schema-driven form — bespoke pages. Gallery and Prompt Library
 *  are the only two that are genuinely their own thing. */
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
