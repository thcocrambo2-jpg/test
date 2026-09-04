// The variance block lives beside the sampler block: the two are duplicated
// together in ui.py, are read together, and share `ordered()`. Kept as its own
// module path because the suggested structure names both.
export { VariancePanel } from '@/components/SamplerPanel'
