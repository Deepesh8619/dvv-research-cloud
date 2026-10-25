import { useEffect, useRef, useState } from 'react'

import { API_BASE, authHeaders } from '../api/client'

export interface StudyEvent { id: number; kind: string; data: unknown }

/** Parse as many complete SSE frames as `buf` holds; returns them and the unread rest. */
export function parseFrames(buf: string): { events: StudyEvent[]; rest: string } {
  const parts = buf.replace(/\r\n/g, '\n').split('\n\n')
  const rest = parts.pop() ?? ''
  const events: StudyEvent[] = []
  for (const part of parts) {
    let id = NaN, kind = 'message', data = ''
    for (const line of part.split('\n')) {
      if (line.startsWith(':')) continue // keepalive comment
      const i = line.indexOf(':')
      const [k, v] = i < 0 ? [line, ''] : [line.slice(0, i), line.slice(i + 1).replace(/^ /, '')]
      if (k === 'id') id = Number(v)
      else if (k === 'event') kind = v
      else if (k === 'data') data += (data ? '\n' : '') + v
    }
    if (!Number.isNaN(id)) {
      let parsed: unknown = data
      try { parsed = JSON.parse(data) } catch { /* keep the raw text */ }
      events.push({ id, kind, data: parsed })
    }
  }
  return { events, rest }
}

/**
 * Live events for one study, read by fetch streaming (EventSource cannot send the
 * Authorization header). When the stream closes it reconnects with `after=<last id>`;
 * after an error it waits 1 s, then 2, 4 ... up to 30 s. Returns whether it is connected.
 */
export function useStudyEvents(studyId: string | undefined, onEvent: (e: StudyEvent) => void): boolean {
  const [connected, setConnected] = useState(false)
  const cb = useRef(onEvent)
  cb.current = onEvent

  useEffect(() => {
    if (!studyId) return
    const ctl = new AbortController()
    let last = 0
    let delay = 1000
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))
    ;(async () => {
      while (!ctl.signal.aborted) {
        try {
          const res = await fetch(`${API_BASE}/studies/${studyId}/events?after=${last}`, {
            headers: authHeaders(), signal: ctl.signal,
          })
          if (!res.ok || !res.body) throw new Error(`events ${res.status}`)
          setConnected(true)
          delay = 1000
          const reader = res.body.getReader()
          const dec = new TextDecoder()
          let buf = ''
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buf += dec.decode(value, { stream: true })
            const { events, rest } = parseFrames(buf)
            buf = rest
            for (const ev of events) { last = ev.id; cb.current(ev) }
          }
          setConnected(false)
          await sleep(100) // closed cleanly (the server's lifetime): reconnect almost at once
        } catch {
          if (ctl.signal.aborted) return
          setConnected(false)
          await sleep(delay)
          delay = Math.min(delay * 2, 30000)
        }
      }
    })()
    return () => ctl.abort()
  }, [studyId])

  return connected
}
