import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  questionnaireApi: {
    translations: vi.fn().mockResolvedValue({ data: { n_strings: 570, languages: [
      { lang: 'hi', n_total: 570, n_translated: 560, n_problems: 12, n_drafts: 40 }],
      draft_job: { state: 'done', lang: 'hi', drafted: 40, refused: 2, remaining: 0 } } }),
    draftTranslation: vi.fn().mockResolvedValue({ data: {} }),
    uploadTranslation: vi.fn().mockResolvedValue({ data: {
      lang: 'hi', n_total: 570, n_translated: 570, n_problems: 1,
      problems: [{ key: 'GA_AI_1/r/1', problem: 'keep [INSERT PROVIDER] exactly as in the source' }] } }),
  },
  downloadFile: vi.fn().mockResolvedValue(undefined),
}))

import { downloadFile, questionnaireApi } from '../api/client'
import TranslationsPanel from './TranslationsPanel'

describe('TranslationsPanel', () => {
  it('shows progress, downloads the sheet, lists what an upload broke', async () => {
    render(<TranslationsPanel studyId="s1" />)
    expect(await screen.findByText('560 of 570 translated')).toBeTruthy()
    const download = screen.getByRole('button', { name: 'Download sheet' }) as HTMLButtonElement
    expect(download.disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'select' }))
    fireEvent.click(download)
    expect(downloadFile).toHaveBeenCalledWith('/studies/s1/questionnaire/translations/hi.csv', 'translation_hi.csv')
    const file = new File(['key,translation'], 'hi.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText('Upload filled sheet'), { target: { files: [file] } })
    expect(questionnaireApi.uploadTranslation).toHaveBeenCalledWith('s1', 'hi', file)
    expect(await screen.findByText(/keep \[INSERT PROVIDER\] exactly/)).toBeTruthy()
  })

  it('drafts the blanks with a model and says what is left to check', async () => {
    render(<TranslationsPanel studyId="s1" />)
    expect(await screen.findByText(/40 model drafts to check/)).toBeTruthy()
    expect(screen.getByText(/2 refused for losing a token/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'select' }))
    fireEvent.click(screen.getByRole('button', { name: 'Draft blanks with a model' }))
    expect(questionnaireApi.draftTranslation).toHaveBeenCalledWith('s1', 'hi')
  })
})
