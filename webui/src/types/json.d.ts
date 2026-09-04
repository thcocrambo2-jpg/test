// JSON is imported as opaque data and cast to a declared type at the point of
// use (see mock/fromBaseline.ts). Left untyped on purpose: letting TypeScript
// infer a literal type for an 87 KB baseline makes every typecheck slow for a
// shape the code re-declares anyway.
declare module '*.json' {
  const value: unknown
  export default value
}
