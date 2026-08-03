import './i18n/index.js'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Auth (DFT-082) was added after many raw `fetch('/api/...')` call sites that never
// attach the bearer token, so they'd all 401 (saving a group announcement, editing/
// deleting messages, member/workflow/workspace ops, ...). Rather than touch every
// site, inject the token globally for same-origin /api/ requests. authFetch already
// sets the header, so we only add it when it's missing.
const _origFetch = window.fetch.bind(window)
window.fetch = (input, init = {}) => {
  let url = typeof input === 'string' ? input : (input && input.url) || ''
  
  // Under file:// protocol in production Electron, resolve relative /api/ and /uploads/ to http://127.0.0.1:8000
  const isFileProto = typeof window !== 'undefined' && (window.location.protocol === 'file:' || !window.location.host)
  if (isFileProto && (url.startsWith('/api/') || url.startsWith('/uploads/'))) {
    url = `http://127.0.0.1:8000${url}`
    if (typeof input === 'string') {
      input = url
    } else if (input && typeof input === 'object') {
      input = new Request(url, input)
    }
  }

  const token = localStorage.getItem('token')
  const isApi = url.startsWith('/api/') || url.includes('/api/')
  if (isApi && token) {
    const headers = new Headers(init.headers || (typeof input !== 'string' ? input.headers : undefined))
    if (!headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`)
      init = { ...init, headers }
    }
  }
  return _origFetch(input, init)
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
