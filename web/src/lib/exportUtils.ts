// Copy/export helpers for chat tool-result toolbars (ChatBot.tsx).
// No external CSV library — the escaping rule set is small enough to hand-roll:
// quote any field containing a comma, quote, or newline, doubling embedded quotes.

export function toCSV(columns: string[], rows: Record<string, unknown>[]): string {
  const escape = (value: unknown): string => {
    const s = value == null ? '' : String(value)
    if (/[",\n]/.test(s)) {
      return `"${s.replace(/"/g, '""')}"`
    }
    return s
  }
  const lines = [columns.map(escape).join(',')]
  for (const row of rows) {
    lines.push(columns.map((c) => escape(row[c])).join(','))
  }
  return lines.join('\n')
}

/** A table given as a header and rows of cells, as CSV (header names may repeat). */
export function rowsToCSV(header: string[], rows: string[][]): string {
  const escape = (value: unknown): string => {
    const s = value == null ? '' : String(value)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [header, ...rows].map((r) => r.map(escape).join(',')).join('\n')
}

export function toMarkdownTable(columns: string[], rows: (string | number)[][]): string {
  const escape = (v: unknown) => String(v ?? '').replace(/\|/g, '\\|')
  const header = `| ${columns.map(escape).join(' | ')} |`
  const divider = `| ${columns.map(() => '---').join(' | ')} |`
  const body = rows.map((r) => `| ${r.map(escape).join(' | ')} |`).join('\n')
  return [header, divider, body].join('\n')
}

export function downloadBlob(filename: string, content: string | Blob, mime: string): void {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
