import { useEffect, useCallback } from 'react'

export function useNotifications() {
  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  const notify = useCallback((title, body) => {
    if (!('Notification' in window)) return
    if (Notification.permission !== 'granted') return
    if (!document.hidden) return

    const n = new Notification(title, {
      body: body?.slice(0, 100) || '',
      icon: '/favicon.ico',
      tag: 'chat-message',
    })
    n.onclick = () => { window.focus(); n.close() }
    setTimeout(() => n.close(), 6000)
  }, [])

  return { notify }
}
