import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api/client', () => ({
  studiesApi: {
    fieldedData: vi.fn().mockResolvedValue({
      data: {
        available: true, processing: false, source_filename: '260578.xlsx', n_respondents: 750,
        n_variables: 696, status_variable: 'status',
        dispositions: [
          { code: 3, label: 'Qualified', outcome: 'complete', n: 254 },
          { code: 1, label: 'Terminated', outcome: 'terminated', n: 420 },
        ],
      },
    }),
    attachData: vi.fn(),
    get: vi.fn(),
  },
}))

import FieldedDataPanel from './FieldedDataPanel'

describe('FieldedDataPanel', () => {
  it('shows an export from another platform as this survey’s fieldwork', async () => {
    render(<MemoryRouter><FieldedDataPanel studyId="s1" canWrite /></MemoryRouter>)
    expect(await screen.findByText('260578.xlsx')).toBeTruthy()
    expect(screen.getByText('750')).toBeTruthy()
    expect(screen.getByText('Qualified')).toBeTruthy()
    expect(screen.getByText('Check it against the questionnaire')).toBeTruthy()
    expect(screen.getByText('Replace data file')).toBeTruthy()
  })
})
