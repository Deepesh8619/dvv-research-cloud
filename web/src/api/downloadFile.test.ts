/**
 * Downloads go through the API client, not a plain <a href>.
 *
 * The Excel and PowerPoint buttons were `<a href="/api/studies/.../download/excel">`.
 * That relative path carries neither of the two things the request needs: the
 * JWT (the endpoint requires a Bearer token) and the configured base URL.
 * Deployed, the frontend is on a different origin from the backend, so the path
 * hit the SPA rewrite and handed the user index.html named "excel".
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'

import { downloadFile } from './client'

vi.mock('axios', async () => {
  const actual = await vi.importActual<typeof import('axios')>('axios')
  const get = vi.fn()
  return {
    ...actual,
    default: {
      ...actual.default,
      create: () => ({
        get,
        post: vi.fn(),
        put: vi.fn(),
        patch: vi.fn(),
        delete: vi.fn(),
        interceptors: {
          request: { use: vi.fn() },
          response: { use: vi.fn() },
        },
        __get: get,
      }),
    },
  }
})

describe('downloadFile', () => {
  let clicked: HTMLAnchorElement | null = null

  beforeEach(() => {
    clicked = null
    URL.createObjectURL = vi.fn(() => 'blob:fake')
    URL.revokeObjectURL = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked = this
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('names the file from the response, not from the caller', async () => {
    const axios = (await import('axios')).default
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const instance = (axios.create as any)()
    instance.__get.mockResolvedValue({
      data: new Blob(['x']),
      headers: { 'content-disposition': 'attachment; filename="Wave 2 QRE.docx"' },
    })

    await downloadFile('/studies/s1/questionnaire/source', 'fallback.docx')

    expect(clicked?.download).toBe('Wave 2 QRE.docx')
    expect(clicked?.href).toContain('blob:')
  })

  it('falls back to the caller name when the server sends no filename', async () => {
    const axios = (await import('axios')).default
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const instance = (axios.create as any)()
    instance.__get.mockResolvedValue({ data: new Blob(['x']), headers: {} })

    await downloadFile('/studies/s1/download/excel', 'Client.xlsx')

    expect(clicked?.download).toBe('Client.xlsx')
  })
})
