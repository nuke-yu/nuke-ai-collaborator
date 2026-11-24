import { act, renderHook } from '@testing-library/react'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import { useWebSocket } from './useWebSocket'

vi.mock('../wsrpc', () => ({ setSocket: vi.fn(), handleFrame: () => false }))
class Socket {
  static OPEN = 1
  static all = []
  readyState = 0
  send = vi.fn()
  constructor() { Socket.all.push(this) }
  close() { this.readyState = 3; this.onclose?.() }
  open() { this.readyState = 1; this.onopen?.() }
  receive(frame) { this.onmessage?.({ data: JSON.stringify(frame) }) }
}
beforeEach(() => {
  vi.useFakeTimers()
  Socket.all = []
  vi.stubGlobal('WebSocket', Socket)
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('resends unacknowledged messages with the same ID and stops after ACK', () => {
  const { result, unmount } = renderHook(() => useWebSocket(1, 2, vi.fn(), vi.fn(), 'token'))
  act(() => Socket.all[0].open())
  act(() => result.current.send('hello'))
  const frame = JSON.parse(Socket.all[0].send.mock.calls[0][0])
  expect(frame.client_message_id).toBeTruthy()
  act(() => { Socket.all[0].close(); vi.advanceTimersByTime(4500) })
  act(() => Socket.all[1].open())
  expect(JSON.parse(Socket.all[1].send.mock.calls[0][0])).toEqual(frame)
  act(() => Socket.all[1].receive({ type: 'client_message_ack', member_id: 2, client_message_id: frame.client_message_id }))
  act(() => { Socket.all[1].close(); vi.advanceTimersByTime(7500) })
  act(() => Socket.all[2].open())
  expect(Socket.all[2].send).not.toHaveBeenCalled()
  unmount()
})

it('never resends another Groups pending messages', () => {
  const { result, rerender, unmount } = renderHook(({ group }) => useWebSocket(group, 2, vi.fn(), vi.fn(), 'token'),
                                                 { initialProps: { group: 1 } })
  act(() => Socket.all[0].open())
  act(() => result.current.send('private group one'))
  rerender({ group: 9 })
  act(() => Socket.all[1].open())
  expect(Socket.all[1].send).not.toHaveBeenCalled()
  unmount()
})
