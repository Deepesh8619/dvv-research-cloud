import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import SourceTile from './SourceTile'
import { blockIndexOf, sliceForQuestion } from './useRenderedSource'

const HTML = [
  '<p id="b0" class="dvv-blk">Q1. How old are you?</p>',
  '<h3 id="b1" class="dvv-blk">DA_SHARE</h3>',
  '<table id="b2" class="dvv-blk dvv-tbl"><tr><td>Q Name</td><td>DA_SHARE</td></tr>' +
    '<tr><td>1</td><td>Google Ads</td></tr></table>',
  '<p class="dvv-blk dvv-gone">Q13b. Struck out entirely</p>',
  '<p id="b3" class="dvv-blk">Q2. Next question</p>',
].join('\n')

const DOC = { filename: 'QRE.docx', n_blocks: 4, html: HTML }

describe('the slice of the document a question was read from', () => {
  it('runs from where the question starts to where the next one starts', () => {
    // Taking only the first block would show a spec table's header row and
    // none of its answers — the half that matters when checking the parse.
    const slice = sliceForQuestion(HTML, blockIndexOf('para 1'), blockIndexOf('para 3'))
    expect(slice).toContain('DA_SHARE')
    expect(slice).toContain('Google Ads')
    expect(slice).not.toContain('Q1. How old are you?')
    expect(slice).not.toContain('Q2. Next question')
  })

  it('keeps a struck-out line that sits inside the question', () => {
    // It carries no id because the loader never emitted it — and "the question
    // was struck out" is exactly what a reviewer is looking for.
    const slice = sliceForQuestion(HTML, blockIndexOf('para 1'), blockIndexOf('para 3'))
    expect(slice).toContain('Struck out entirely')
  })

  it('runs to the end when nothing follows', () => {
    const slice = sliceForQuestion(HTML, blockIndexOf('para 3'), null)
    expect(slice).toContain('Q2. Next question')
  })

  it('gives nothing when the question has no paragraph provenance', () => {
    expect(sliceForQuestion(HTML, blockIndexOf('data map'), null)).toBe('')
  })
})

describe('the source tile', () => {
  it('draws the table as a table, not as flattened lines', () => {
    render(
      <SourceTile
        sourceText={'Q Name: DA_SHARE\n1: Google Ads'}
        sourceLocation="para 1"
        nextLocation="para 3"
        doc={DOC}
      />,
    )
    expect(document.querySelector('table')).toBeTruthy()
    expect(screen.getByText('Google Ads')).toBeTruthy()
  })

  it('falls back to the parser’s own text when the document is unavailable', () => {
    render(
      <SourceTile
        sourceText={'Q Name: DA_SHARE\n1: Google Ads'}
        sourceLocation="para 1"
        nextLocation={null}
        doc={null}
      />,
    )
    expect(document.querySelector('table')).toBeNull()
    expect(screen.getByText(/Q Name: DA_SHARE/)).toBeTruthy()
  })
})
