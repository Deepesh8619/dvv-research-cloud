import { describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'

vi.mock('../api/client', () => ({
  studiesApi: {
    fieldedData: vi.fn()
      .mockResolvedValueOnce({ data: { available: false, processing: false } })
      .mockResolvedValue({ data: { available: true, processing: false, source_filename: 'fielded.xlsx',
                                   n_respondents: 254, n_variables: 696 } }),
    attachData: vi.fn().mockResolvedValue({ data: { status: 'classifying' } }),
    get: vi.fn().mockResolvedValue({ data: { status: 'qc_passed' } }),
    download: vi.fn(),
  },
}))

import ProjectDataBar, { DATA_CHANGED } from './ProjectDataBar'
import { studiesApi } from '../api/client'

describe('ProjectDataBar', () => {
  it('is the one place data is uploaded, and tells every panel when it changes', async () => {
    vi.useFakeTimers()
    const changed = vi.fn()
    window.addEventListener(DATA_CHANGED, changed)
    render(<ProjectDataBar studyId="s1" canWrite />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText(/No data yet/)).toBeTruthy()
    const file = new File(['x'], 'fielded.xlsx')
    await act(async () => { fireEvent.change(screen.getByLabelText('Project data file'), { target: { files: [file] } }) })
    expect(studiesApi.attachData).toHaveBeenCalledWith('s1', file, expect.any(Function))
    await act(async () => { await vi.advanceTimersByTimeAsync(3100) })
    expect(screen.getByText('fielded.xlsx')).toBeTruthy()
    expect(screen.getByText('254 respondents · 696 variables')).toBeTruthy()
    expect(changed).toHaveBeenCalled()
    vi.useRealTimers()
  })
})
