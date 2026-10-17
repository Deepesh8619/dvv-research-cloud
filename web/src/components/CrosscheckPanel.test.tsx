import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

vi.mock('../api/client', () => ({
  questionnaireApi: { status: vi.fn().mockResolvedValue({ data: { source_filename: 'qre.docx', version: 1 } }) },
  studiesApi: {
    storedCrosscheck: vi.fn().mockResolvedValue({ data: { report: null } }),
    attachData: vi.fn().mockResolvedValue({ data: { status: 'classifying', source: 'fielded.xlsx' } }),
    get: vi.fn().mockResolvedValue({ data: { status: 'qc_passed' } }),
    download: vi.fn(),
    crosscheck: vi.fn(),
  },
}))

import CrosscheckPanel from './CrosscheckPanel'
import { studiesApi } from '../api/client'

describe('CrosscheckPanel data file', () => {
  it('lets a questionnaire-first project upload its data file and says when it is ready', async () => {
    vi.useFakeTimers()
    render(<CrosscheckPanel studyId="s1" dataFilename={null} />)
    const input = document.getElementById('crosscheck-data-file') as HTMLInputElement
    const file = new File(['x'], 'fielded.xlsx')
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } })
    })
    expect(studiesApi.attachData).toHaveBeenCalledWith('s1', file, expect.any(Function))
    expect(screen.getByText(/Reading the data file/)).toBeTruthy()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3100)
    })
    expect(screen.getByText(/Data file ready/)).toBeTruthy()
    expect(screen.getByText('fielded.xlsx')).toBeTruthy()
    vi.useRealTimers()
  })
})
