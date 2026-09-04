import type { Catalogue, Cycle, Plan } from '@/api/types'

/*
 * A stand-in for GET /v1/plans on the licence server.
 *
 * Shaped exactly like `plans.Catalogue` (plans.py:130) so the pricing page is
 * written against the real dataclass and not against a convenience. In
 * particular every per-cycle figure is precomputed here the way `cyclePrice`
 * computes it server-side — the page renders numbers, it never derives them,
 * because two implementations of the same arithmetic is two chances to quote
 * a price the invoice does not match (plans.py:69).
 */

const CYCLES: Cycle[] = [
  { id: 'monthly', label: 'Monthly', months: 1, discount_percent: 0 },
  { id: 'quarterly', label: 'Quarterly', months: 3, discount_percent: 10 },
  { id: 'yearly', label: 'Yearly', months: 12, discount_percent: 25 },
]

function priced(monthly: number) {
  return Object.fromEntries(
    CYCLES.map((cycle) => {
      const gross = monthly * cycle.months
      const total = Math.round(gross * (1 - cycle.discount_percent / 100))
      return [
        cycle.id,
        {
          cycle: cycle.id,
          months: cycle.months,
          total,
          per_month: Math.round(total / cycle.months),
          discount_percent: cycle.discount_percent,
          saving: gross - total,
        },
      ]
    }),
  )
}

const PLANS: Plan[] = [
  {
    id: 'starter',
    name: 'Starter',
    description: 'The two text-to-image engines, the gallery and the prompt library.',
    price_monthly: 1499,
    currency: 'INR',
    features: ['krea_t2i', 'krea_v2_t2i', 'gallery', 'community_prompts'],
    sort_order: 1,
    prices: priced(1499),
    is_popular: false,
  },
  {
    id: 'studio',
    name: 'Studio',
    description: 'Everything in Starter, plus the whole editing suite.',
    price_monthly: 3499,
    currency: 'INR',
    features: [
      'krea_t2i',
      'krea_v2_t2i',
      'gallery',
      'community_prompts',
      'krea_edit',
      'krea_v2_edit',
      'krea_inpaint',
      'faceswap',
      'json_batch',
    ],
    sort_order: 2,
    prices: priced(3499),
    is_popular: true,
  },
  {
    id: 'complete',
    name: 'Complete',
    description: 'Every tab, including Flux, Klein Edit and Wan video.',
    price_monthly: 5999,
    currency: 'INR',
    features: [
      'krea_t2i',
      'krea_v2_t2i',
      'gallery',
      'community_prompts',
      'krea_edit',
      'krea_v2_edit',
      'krea_inpaint',
      'faceswap',
      'json_batch',
      'flux_t2i',
      'klein_i2i',
      'wan_i2v',
    ],
    sort_order: 3,
    prices: priced(5999),
    is_popular: false,
  },
]

/** The features registry, as `plans.Catalogue.features`. Prose names, which
 *  are what the pricing page lists — not the terse tab labels. */
const FEATURES: Catalogue['features'] = Object.fromEntries(
  (
    [
      ['krea_t2i', 'Text to image', 'Type a sentence, get a photograph.', 'generation'],
      ['krea_v2_t2i', 'Text to image V2', 'The V2 pipeline and its full sampler stack.', 'generation'],
      ['flux_t2i', 'Flux 2D', 'A second engine with a different look.', 'generation'],
      ['krea_edit', 'Instruction editing', 'Change one thing, keep everything else.', 'editing'],
      ['krea_v2_edit', 'Instruction editing V2', 'The same recipe on the V2 pipeline.', 'editing'],
      ['krea_inpaint', 'Inpainting', 'Paint a region and replace only that.', 'editing'],
      ['klein_i2i', 'Klein editing', 'Two reference images, one result.', 'editing'],
      ['faceswap', 'Face swap', 'One face, any photograph.', 'editing'],
      ['wan_i2v', 'Video', 'Turn a still into a few seconds of motion.', 'video'],
      ['gallery', 'Gallery', 'Everything you have made, browsable.', 'tools'],
      ['community_prompts', 'Prompt library', 'Prompts that already work.', 'tools'],
      ['json_batch', 'JSON batch', 'Run a graph straight through.', 'tools'],
    ] as const
  ).map(([key, name, description, category]) => [key, { key, name, description, category }]),
)

export const MOCK_CATALOGUE: Catalogue = {
  plans: PLANS,
  features: FEATURES,
  cycles: CYCLES,
  error: null,
  contact_url: 'https://t.me/ember_support',
  owned: ['krea_t2i', 'krea_v2_t2i', 'krea_inpaint', 'gallery', 'community_prompts'],
}
