import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import SoftLaunchPanel from './SoftLaunchPanel'

/**
 * The distinction this panel exists to protect: "we looked and found nothing"
 * versus "we could not look". Rendering the second as a pass is how a broken
 * script gets released to the full sample.
 */
const base = {
  link_id: 'l1',
  soft_launch_limit: 50,
  held: true,
  n_completes: 50,
  n_checked_rows: 63,
  crosscheck: {
    passed: true,
    n_rows: 63,
    checked: { codes: 12, terminate: 3 },
    skipped: [],
    findings: [],
  },
  field_report: {},
}

vi.mock('../api/client', () => ({
  fieldingApi: { softLaunchCheck: vi.fn(), setSoftLaunch: vi.fn() },
}))

async function renderWith(check: Record<string, unknown>) {
  const { fieldingApi } = await import('../api/client')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(fieldingApi.softLaunchCheck as any).mockResolvedValue({ data: check })
  render(<SoftLaunchPanel studyId="s1" linkId="l1" canWrite onChanged={() => {}} />)
  return screen.findByText(/rows? checked/)
}

describe('SoftLaunchPanel', () => {
  it('passes the batch only when checks actually ran', async () => {
    await renderWith(base)
    expect(await screen.findByText(/does what the questionnaire says/)).toBeTruthy()
    expect(screen.getByText(/12 codes, 3 terminate checked/)).toBeTruthy()
  })

  it('refuses to call "no checks ran" a pass', async () => {
    await renderWith({
      ...base,
      crosscheck: { ...base.crosscheck, checked: {}, skipped: ['no data file'] },
    })
    expect(await screen.findByText(/No check could run yet/)).toBeTruthy()
    expect(screen.getByText(/This is not/)).toBeTruthy()
    expect(screen.queryByText(/does what the questionnaire says/)).toBeNull()
  })

  it('blocks confidence when the data and the script disagree', async () => {
    await renderWith({
      ...base,
      crosscheck: {
        ...base.crosscheck,
        passed: false,
        findings: [
          {
            qid: 'S1',
            kind: 'terminate_violated',
            severity: 'error',
            detail: 'S1 screens these respondents out, but they went on answering.',
            n_rows: 4,
            variables: ['S1'],
          },
        ],
      },
    })
    expect(await screen.findByText(/Fix the script before releasing/)).toBeTruthy()
    expect(screen.getByText(/Releasing now fields a script with known errors/)).toBeTruthy()
  })

  it('offers release only while a hold is actually set', async () => {
    await renderWith({ ...base, soft_launch_limit: null, held: false })
    expect(await screen.findByText('Re-run check')).toBeTruthy()
    expect(screen.queryByText('Release to full sample')).toBeNull()
  })
})
