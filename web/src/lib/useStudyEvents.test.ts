import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { parseFrames, useStudyEvents } from './useStudyEvents'

const stream = (text: string) =>
  new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(new TextEncoder().encode(text)); c.close() },
  })

afterEach(() => vi.unstubAllGlobals())

describe('parseFrames', () => {
  it('reads id/event/data, skips keepalives, keeps a partial frame', () => {
    const { events, rest } = parseFrames(
      ': keepalive\n\nid: 3\nevent: respondent.complete\ndata: {"a":1}\n\nid: 4\nevent: x',
    )
    expect(events).toEqual([{ id: 3, kind: 'respondent.complete', data: { a: 1 } }])
    expect(rest).toBe('id: 4\nevent: x')
  })
})

describe('useStudyEvents', () => {
  it('delivers events and reconnects with the last id', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, body: stream('id: 7\nevent: respondent.complete\ndata: {}\n\n') })
      .mockResolvedValue({ ok: true, body: stream(': keepalive\n\n') })
    vi.stubGlobal('fetch', fetchMock)
    const got: number[] = []
    const { unmount } = renderHook(() => useStudyEvents('s1', (e) => got.push(e.id)))
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1))
    expect(got).toEqual([7])
    expect(String(fetchMock.mock.calls[0][0])).toContain('/studies/s1/events?after=0')
    expect(String(fetchMock.mock.calls[1][0])).toContain('after=7')
    unmount()
  })
})
