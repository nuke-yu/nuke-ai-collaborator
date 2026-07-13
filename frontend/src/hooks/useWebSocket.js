import { useEffect, useRef, useCallback, useState } from 'react'
import * as wsrpc from '../wsrpc'

// Exponential backoff: 3s → 6s → 12s → 24s → cap 30s, with random jitter
const _BACKOFF_BASE = 3000
const _BACKOFF_MAX = 30000
// Only reset backoff after connection has been stable this long
const _STABLE_CONNECTION_MS = 30000

function _nextBackoff(attempt) {
  const delay = Math.min(_BACKOFF_BASE * Math.pow(2, attempt), _BACKOFF_MAX)
  return delay + Math.random() * 1000 // jitter up to 1s
}

export function useWebSocket(groupId, memberId, onMessage, onReconnect, token, onAuthError) {
  const ws = useRef(null)
  const retryTimer = useRef(null)
  const stableTimer = useRef(null)
  const attemptRef = useRef(0)
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const reconnectingRef = useRef(false)
  const identityRef = useRef(null)
  const connectRef = useRef(null)

  const onMessageRef = useRef(onMessage)
  const onReconnectRef = useRef(onReconnect)
  const onAuthErrorRef = useRef(onAuthError)
  useEffect(() => {
    onMessageRef.current = onMessage
    onReconnectRef.current = onReconnect
    onAuthErrorRef.current = onAuthError
  }, [onMessage, onReconnect, onAuthError])

  const connect = useCallback(() => {
    if (!groupId || !memberId) return
    const identity = `${groupId}:${memberId}:${token}`
    if (identityRef.current !== identity) {
      identityRef.current = identity
      attemptRef.current = 0
      reconnectingRef.current = false
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host || 'localhost:8000'
    const url = protocol + '//' + host + '/ws/' + groupId + '/' + memberId
    const socket = new WebSocket(url, token ? [`nuke.jwt.${token}`] : undefined)

    socket.onopen = () => {
      if (ws.current !== socket) return
      wsrpc.setSocket(socket)
      if (reconnectingRef.current) {
        onReconnectRef.current?.()
      }
      setConnected(true)
      setReconnecting(false)
      reconnectingRef.current = false
      // Only reset backoff after connection is stable for _STABLE_CONNECTION_MS
      clearTimeout(stableTimer.current)
      stableTimer.current = setTimeout(() => {
        attemptRef.current = 0
      }, _STABLE_CONNECTION_MS)
    }

    socket.onmessage = (e) => {
      if (ws.current !== socket) return
      const data = JSON.parse(e.data)
      if (data.type === 'auth_error') {
        onAuthErrorRef.current?.(data.message)
        socket.onclose = null // prevent retry
        socket.close()
        return
      }
      if (wsrpc.handleFrame(data)) return
      onMessageRef.current(data)
      // 收到他人消息时，立即发回已读确认
      if (data.type === 'message' && data.id) {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'read', msg_id: data.id }))
        }
      }
    }

    socket.onclose = () => {
      if (ws.current !== socket) return
      wsrpc.setSocket(null)
      clearTimeout(stableTimer.current)
      setConnected(false)
      setReconnecting(true)
      reconnectingRef.current = true
      const delay = _nextBackoff(attemptRef.current)
      attemptRef.current += 1
      retryTimer.current = setTimeout(() => connectRef.current?.(), delay)
    }

    socket.onerror = () => {
      if (ws.current !== socket) return
      socket.close()
    }

    ws.current = socket
  }, [groupId, memberId, token])

  useEffect(() => {
    connectRef.current = connect
  }, [connect])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(retryTimer.current)
      clearTimeout(stableTimer.current)
      if (ws.current) {
        ws.current.onclose = null
        wsrpc.setSocket(null)
        ws.current.close()
        ws.current = null
      }
    }
  }, [connect])

  const send = useCallback((content, replyToId = null, fileData = null) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        content,
        reply_to_id: replyToId,
        lang: localStorage.getItem('lang') || 'zh',
        ...(fileData && { file_url: fileData.url, file_name: fileData.name, file_size: fileData.size, file_type: fileData.type }),
      }))
    }
  }, [])

  const sendRaw = useCallback((payload) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(payload))
    }
  }, [])

  return { send, sendRaw, connected, reconnecting }
}
