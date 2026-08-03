import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ackPersonalRecap } from './api'

describe('ackPersonalRecap (TOCTOU fix wiring)', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }), text: () => Promise.resolve('') })
    )
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('sends covered_through_id in the POST body so ack pins to the recap-covered id, not MAX(id) at click time', async () => {
    await ackPersonalRecap(1, 5, 100)

    expect(global.fetch).toHaveBeenCalledTimes(1)
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/groups/1/recap/ack/5')
    expect(opts.method).toBe('POST')
    expect(opts.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(opts.body)).toEqual({ covered_through_id: 100 })
  })
})
