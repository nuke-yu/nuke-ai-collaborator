import { useEffect, useRef, useCallback, useState } from 'react'
import * as wsrpc from '../wsrpc'

export function useWebSocket(groupId, memberId, onMessage, onReconnect, token, onAuthError) {
  const ws = useRef(null)
  const retryTimer = useRef(null)
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)

  const onMessageRef = useRef(onMessage)
  const onReconnectRef = useRef(onReconnect)
  const onAuthErrorRef = useRef(onAuthError)
  useEffect(() => {
    onMessageRef.current = onMessage
    onReconnectRef.current = onReconnect
    onAuthErrorRef.current = onAuthError
  }, [onMessage, onReconnect])

  const connect = useCallback(() => {
    if (!groupId || !memberId) return
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host || 'localhost:8000'
    const url = protocol + '//' + host + '/ws/' + groupId + '/' + memberId + '?token=' + token
    const socket = new WebSocket(url)

    socket.onopen = () => {
      wsrpc.setSocket(socket)
      if (reconnecting) {
        onReconnectRef.current?.()
      }
      setConnected(true)
      setReconnecting(false)
    }

    socket.onmessage = (e) => {
      const data = JSON.parse(e.data)
      if (data.type === 'auth_error') {
        onAuthErrorRef.current?.(data.message)
        if (ws.current) ws.current.onclose = null // prevent retry
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
      setConnected(false)
      setReconnecting(true)
      retryTimer.current = setTimeout(connect, 3000)
    }

    socket.onerror = () => {
      socket.close()
    }

    ws.current = socket
  }, [groupId, memberId])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(retryTimer.current)
      if (ws.current) {
        ws.current.onclose = null
        ws.current.close()
      }
    }
  }, [connect])

  const send = useCallback((content, replyToId = null, fileData = null) => {
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({
        content,
        reply_to_id: replyToId,
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
