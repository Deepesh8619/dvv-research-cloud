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
const dryRun = vi.fn()

vi.mock('../api/client', () => ({
  fieldingApi: {
    issueCertificate: vi.fn().mockResolvedValue({ data: { run_id: 'r1' } }),
    workflowRun: (...a: unknown[]) => workflowRun(...a),
    downloadCertificate: vi.fn(),
    dryRun: (...a: unknown[]) => dryRun(...a),
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

const report = (proof: unknown) => ({
  data: {
    n_respondents: 10, seed: 1, passed: true, dispositions: { complete: 10, terminate: 0, overquota: 0 },
    conditions: [], never_fired: [], impossible: [], questions_never_shown: [],
    display_issues: [], unenforced_routing: [], proof,
  },
})

describe('DryRunPanel solver line', () => {
  it('says every question is reachable', async () => {
    dryRun.mockResolvedValue(report({ unreachable: [], not_provable: [] }))
    render(<DryRunPanel studyId="s1" canRun />)
    fireEvent.click(screen.getByText('Run dry run'))
    expect(await screen.findByText(/every question is reachable/)).toBeTruthy()
  })

  it('names a question that can never be shown, with the reason', async () => {
    dryRun.mockResolvedValue(report({
      unreachable: [{ qid: 'Q7', why: 'Q7 needs Q5 = 4, but Q5 only allows 1–3' }], not_provable: [] }))
    render(<DryRunPanel studyId="s1" canRun />)
    fireEvent.click(screen.getByText('Run dry run'))
    expect(await screen.findByText(/Q7 needs Q5 = 4, but Q5 only allows 1–3/)).toBeTruthy()
  })
})
