// The variance block lives beside the sampler block: the two always appear
// together, are read together, and share `ordered()`, so one module renders
// both. This path exists so each block can be imported under its own name.
export { VariancePanel } from '@/components/SamplerPanel'
