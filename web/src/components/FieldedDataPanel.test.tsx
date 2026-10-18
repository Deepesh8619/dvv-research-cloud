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
    dataFieldReport: vi.fn().mockResolvedValue({
      data: {
        available: true, source_filename: '260578.xlsx', has_questionnaire: false, starts: 254,
        dispositions: { complete: 62, terminate: 140, security: 0, overquota: 45, in_progress: 7 },
        incidence_pct: 30.7, median_loi_sec: 1038.5,
        terminates_by_question: [{ qid: 'SS_RESP', n: 90, rule: 'SS_RESP.any and (not(SS_RESP.r1))' }],
        overquota_by_cell: [{ cell: '/Overall/Total', n: 45 }],
        dropoff_by_question: [], devices: { desktop: 150, mobile: 100 }, place_checks: null,
        length_of_interview: { stated_minutes: 12, measured_minutes: 17.3, over: true, note: 'Running long.' },
        sample_size_target: 750,
        quota_targets: [{ group: 'Small', definition: '10-99 Employees', target_n: null, min_n: 100, max_n: 250 }],
        quotas: [{ table: 'Org_size', cells: [{ cell: 'HID_COMP_SIZE_r3', label: 'Small', n: 15 }] }],
        field_dates: { first: '2026-06-01T09:39:00', last: '2026-06-01T15:59:00' },
        completes_by_day: [], quality: { n_flagged: 22, by_check: [{ check: 'gibberish', n: 22 }] },
        questions: [],
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
    expect(screen.getAllByText('Qualified').length).toBeGreaterThan(0)
    expect(screen.getByText('Check it against the questionnaire')).toBeTruthy()
    expect(screen.getByText('Replace data file')).toBeTruthy()
  })

  it('reads the export as fieldwork: incidence, screen-outs, quotas, quality', async () => {
    render(<MemoryRouter><FieldedDataPanel studyId="s1" canWrite /></MemoryRouter>)
    expect(await screen.findByText('30.7%')).toBeTruthy()
    expect(screen.getByText('of 750 needed · 8%')).toBeTruthy()
    expect(screen.getByText('17m 19s')).toBeTruthy()
    expect(screen.getByText('over the 12 min promised')).toBeTruthy()
    expect(screen.getByText('SS_RESP')).toBeTruthy()
    expect(screen.getByText('Org_size')).toBeTruthy()
    expect(screen.getByText('Small · 10-99 Employees')).toBeTruthy()
    expect(screen.getByText('Review them in Cleaning →')).toBeTruthy()
  })
})
