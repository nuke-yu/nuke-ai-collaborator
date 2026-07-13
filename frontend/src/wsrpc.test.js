import { afterEach, describe, expect, it } from 'vitest'

import * as wsrpc from './wsrpc'


function openSocket() {
  return {
    readyState: WebSocket.OPEN,
    sent: [],
    send(payload) {
      this.sent.push(JSON.parse(payload))
    },
  }
}


afterEach(() => {
  wsrpc.setSocket(null)
})


describe('wsrpc request lifecycle', () => {
  it('removes and rejects an aborted request', async () => {
    const socket = openSocket()
    const controller = new AbortController()
    wsrpc.setSocket(socket)

    const pending = wsrpc.request({ query: 'messages' }, { signal: controller.signal })
    const reqId = socket.sent[0].req_id
    controller.abort()

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(wsrpc.handleFrame({ type: 'query_result', req_id: reqId, ok: true, data: {} })).toBe(false)
  })

  it('rejects pending requests when the socket is replaced', async () => {
    const first = openSocket()
    wsrpc.setSocket(first)
    const pending = wsrpc.request({ query: 'messages' })

    wsrpc.setSocket(openSocket())

    await expect(pending).rejects.toThrow('socket replaced')
  })

  it('resolves only the matching response', async () => {
    const socket = openSocket()
    wsrpc.setSocket(socket)
    const pending = wsrpc.request({ query: 'messages' })
    const reqId = socket.sent[0].req_id

    expect(wsrpc.handleFrame({ type: 'query_result', req_id: 'other', ok: true, data: {} })).toBe(false)
    expect(wsrpc.handleFrame({ type: 'query_result', req_id: reqId, ok: true, data: { messages: [] } })).toBe(true)
    await expect(pending).resolves.toEqual({ messages: [] })
  })

  it('cleans up a request when socket.send throws', async () => {
    const socket = openSocket()
    let reqId
    socket.send = (payload) => {
      reqId = JSON.parse(payload).req_id
      throw new Error('transport closed')
    }
    wsrpc.setSocket(socket)

    await expect(wsrpc.request({ query: 'messages' })).rejects.toThrow('transport closed')
    expect(wsrpc.handleFrame({ type: 'query_result', req_id: reqId, ok: true, data: {} })).toBe(false)
  })
})
