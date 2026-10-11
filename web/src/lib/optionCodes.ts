/**
 * Remove answer `index` and close the gap in its run of codes: 1,2,3,4 minus 2
 * is 1,2,3. Codes outside that run (98 "Don't know", 99 "None") keep theirs —
 * they are fixed system codes, not positions. When the reviewer has typed a
 * code themselves (`typed`), their numbering stands and nothing moves.
 */
export function removeAndRenumber<T extends { code: number }>(options: T[], index: number, typed: boolean): T[] {
  const gone = options[index].code
  const codes = new Set(options.map((o) => o.code))
  let runEnd = gone
  while (codes.has(runEnd + 1)) runEnd += 1
  return options
    .filter((_, i) => i !== index)
    .map((o) => (!typed && o.code > gone && o.code <= runEnd ? { ...o, code: o.code - 1 } : o))
}
