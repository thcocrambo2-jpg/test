import { useLayoutEffect, useState } from 'react'

/*
 * Masonry, shared by the Gallery and the pricing page's plan cards.
 *
 * Two halves: a hook that says how many columns fit, and a function that
 * deals items into them. The stylesheet only lays the columns out side by
 * side (a flex row of flex columns), so each page keeps its own gap and
 * tile look and borrows nothing but the arithmetic.
 */

/** How many columns of at least `minWidth` fit in the grid, kept current as
 *  it resizes. Measured before paint so the first frame is already laid out;
 *  the gap is read from the stylesheet so the two cannot disagree. */
export function useColumnCount(el: HTMLElement | null, minWidth: number, fewest: number) {
  const [count, setCount] = useState(fewest)
  useLayoutEffect(() => {
    if (!el) return
    const measure = () => {
      const gap = parseFloat(getComputedStyle(el).columnGap) || 0
      setCount(Math.max(fewest, Math.floor((el.clientWidth + gap) / (minWidth + gap))))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [el, minWidth, fewest])
  return count
}

/** Deal items into columns, each into whichever is shortest so far.
 *
 *  Plain CSS masonry (`column-count`) fills one column top to bottom before
 *  starting the next, so the second item lands under the first and the one
 *  at the top of column two is from the middle of the list. Going to the
 *  shortest column keeps reading left to right, row by row, give or take
 *  the shapes. `height` is an estimate in any unit the caller likes, as long
 *  as it is the same unit for every item: the comparison is all it is for.
 *  Returns indices into `items`, so the caller keeps its own keys. */
export function packColumns<T>(items: readonly T[], count: number, height: (item: T) => number) {
  const columns: number[][] = Array.from({ length: count }, () => [])
  const heights = new Array<number>(count).fill(0)
  items.forEach((item, index) => {
    let shortest = 0
    for (let at = 1; at < count; at += 1) {
      if (heights[at] < heights[shortest] - 1e-6) shortest = at
    }
    columns[shortest].push(index)
    heights[shortest] += height(item)
  })
  return columns
}
