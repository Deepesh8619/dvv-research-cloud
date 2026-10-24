import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

vi.mock('../api/client', () => ({
  questionnaireApi: { status: vi.fn().mockResolvedValue({ data: { source_filename: 'qre.docx', version: 1 } }) },
  studiesApi: {
    storedCrosscheck: vi.fn().mockResolvedValue({ data: { report: null } }),
    download: vi.fn(),
    crosscheck: vi.fn(),
  },
}))

import CrosscheckPanel from './CrosscheckPanel'

describe('CrosscheckPanel data file', () => {
  it('reads the project’s data file and has no upload of its own', async () => {
    const { unmount } = render(<CrosscheckPanel studyId="s1" dataFilename="fielded.xlsx" />)
    expect(await screen.findByText('fielded.xlsx')).toBeTruthy()
    expect(document.querySelector('input[type=file]')).toBeNull()
    unmount()
    render(<CrosscheckPanel studyId="s1" dataFilename={null} />)
    expect(await screen.findByText(/upload it in the Data bar/)).toBeTruthy()
  })
})
