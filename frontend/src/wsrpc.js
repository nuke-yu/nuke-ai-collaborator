// WS request/reply correlation. The supervisor broadcasts `query_result` frames
// to the whole group; we resolve only the pending request whose req_id matches
// (req_id carries a per-tab prefix, so another tab's results are simply ignored).
const _tab = Math.random().toString(36).slice(2, 8)
let _seq = 0
let _socket = null
const _pending = new Map() // req_id -> { resolve, reject, timer }

export function setSocket(ws) {
  _socket = ws
}

// Returns true if the frame was a query_result we consumed (caller should stop).
export function handleFrame(data) {
  if (data && data.type === 'query_result' && _pending.has(data.req_id)) {
    const { resolve, reject, timer } = _pending.get(data.req_id)
    clearTimeout(timer)
    _pending.delete(data.req_id)
    data.ok ? resolve(data.data) : reject(new Error(data.error || 'query failed'))
    return true
  }
  return false
}

export function request(payload, timeoutMs = 10000) {
  return new Promise((resolve, reject) => {
    if (!_socket || _socket.readyState !== WebSocket.OPEN) {
      reject(new Error('socket not open'))
      return
    }
    const req_id = `${_tab}-${++_seq}`
    const timer = setTimeout(() => {
      _pending.delete(req_id)
      reject(new Error('query timeout'))
    }, timeoutMs)
    _pending.set(req_id, { resolve, reject, timer })
    _socket.send(JSON.stringify({ type: 'query', req_id, ...payload }))
  })
}

export function send(payload) {
  if (_socket && _socket.readyState === WebSocket.OPEN) {
    _socket.send(JSON.stringify(payload))
  }
}
