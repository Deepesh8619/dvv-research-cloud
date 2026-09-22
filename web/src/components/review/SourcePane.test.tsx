import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import SourcePane, { blockIndexOf } from './SourcePane'

vi.mock('../../api/client', () => ({
  questionnaireApi: { renderedSource: vi.fn() },
}))

const DOC = {
  filename: 'Wave 2 QRE.docx',
  n_blocks: 3,
  html:
    '<p id="b0" class="dvv-blk">Q1. What is your age?</p>' +
    '<p id="b1" class="dvv-blk">Q2. <span class="dvv-struck">Bubble Co</span>Northwell</p>' +
    '<p id="b2" class="dvv-blk">Q3. How satisfied?</p>',
}

async function renderPane(sourceLocation: string | null) {
  const { questionnaireApi } = await import('../../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(questionnaireApi.renderedSource as any).mockResolvedValue({ data: DOC })
  render(<SourcePane studyId="s1" sourceLocation={sourceLocation} onClose={() => {}} />)
  await waitFor(() => expect(screen.getByText(/Wave 2 QRE/)).toBeTruthy())
}

describe('reading the document beside the extraction', () => {
  it('parses the parser’s own provenance into a block anchor', () => {
    // This is the whole contract between the two halves: get it wrong and
    // every question scrolls the reviewer to the wrong paragraph.
    expect(blockIndexOf('para 47')).toBe(47)
    expect(blockIndexOf('page 3, para 12')).toBe(12)
    expect(blockIndexOf('data map')).toBeNull()
    expect(blockIndexOf(null)).toBeNull()
  })

  it('marks the block the selected question was read from', async () => {
    await renderPane('para 1')
    await waitFor(() => {
      expect(document.querySelector('#b1')?.classList.contains('dvv-here')).toBe(true)
    })
    expect(document.querySelector('#b0')?.classList.contains('dvv-here')).toBe(false)
  })

  it('shows what the parser dropped, rather than the text it kept', async () => {
    await renderPane('para 0')
    // The struck brand is visible AND marked — "the brand is missing" and
    // "the brand was struck out" are different problems.
    expect(screen.getByText('Bubble Co').classList.contains('dvv-struck')).toBe(true)
    expect(screen.getByText(/Northwell/)).toBeTruthy()
  })

  it('says why when the document cannot be opened', async () => {
    const { questionnaireApi } = await import('../../api/client')
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(questionnaireApi.renderedSource as any).mockRejectedValue({
      response: { data: { detail: 'This one is a PDF.' } },
    })
    render(<SourcePane studyId="s1" sourceLocation="para 1" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/This one is a PDF/)).toBeTruthy())
  })
})
