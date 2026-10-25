import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const run = (certificate: Record<string, unknown>, dry: Record<string, unknown>) => ({
  data: {
    id: 'r1', state: 'done', error: null,
    steps: [
      { name: 'expert_checks', state: 'done', error: null, result: {} },
      { name: 'dry_run', state: 'done', error: null, result: dry },
      { name: 'certificate', state: 'done', error: null, result: certificate },
    ],
  },
})
const workflowRun = vi.fn()

vi.mock('../api/client', () => ({
  fieldingApi: {
    issueCertificate: vi.fn().mockResolvedValue({ data: { run_id: 'r1' } }),
    workflowRun: (...a: unknown[]) => workflowRun(...a),
    downloadCertificate: vi.fn(),
  },
}))
vi.mock('./LogicXray', () => ({ default: () => null }))

import DryRunPanel from './DryRunPanel'

describe('DryRunPanel certificate', () => {
  it('offers the download once issued', async () => {
    workflowRun.mockResolvedValue(run({ issued: true }, {}))
    render(<DryRunPanel studyId="s1" canRun />)
    fireEvent.click(screen.getByText('Issue logic certificate'))
    expect(await screen.findByText('download')).toBeTruthy()
  })

  it('lists the blockers when not issued', async () => {
    workflowRun.mockResolvedValue(run({ issued: false }, { questions_never_shown: ['Q9'], impossible: [] }))
    render(<DryRunPanel studyId="s1" canRun />)
    fireEvent.click(screen.getByText('Issue logic certificate'))
    expect(await screen.findByText(/Not issued/)).toBeTruthy()
    expect(screen.getByText('Q9 can never be reached')).toBeTruthy()
  })
})
