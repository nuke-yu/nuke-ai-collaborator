import * as wsrpc from './wsrpc'

async function authFetch(url, options = {}) {
  const token = localStorage.getItem('token')
  const headers = { ...options.headers }
  if (token) {
    headers['Authorization'] = 'Bearer ' + token
  }
  const res = await fetch(url, { ...options, headers })
  if (res.status === 401 && !url.includes('/api/auth/')) {
    localStorage.removeItem('token')
    window.location.reload()
  }
  return res
}
export async function fetchAllGroups() {
  const res = await authFetch('/api/groups')
  return res.json()
}

export async function fetchGroupInfo(groupId) {
  const res = await authFetch(`/api/groups/${groupId}`)
  return res.json()
}

export async function fetchReactions(groupId) {
  return wsrpc.request({ query: 'reactions', group_id: groupId })
}

export async function toggleReaction(msgId, memberId, emoji) {
  // memberId kept in the signature for caller compatibility but ignored:
  // the server uses the authenticated connection's member_id.
  wsrpc.send({ type: 'mutate', action: 'toggle_reaction', msg_id: msgId, emoji })
}

export async function searchMessages(groupId, q) {
  return wsrpc.request({ query: 'search', group_id: groupId, q })
}

export async function fetchUnreadCounts(memberId) {
  const res = await authFetch(`/api/members/${memberId}/unread`)
  return res.json()
}

export async function fetchMessages(groupId, { beforeId, afterId } = {}) {
  return wsrpc.request({
    query: 'messages', group_id: groupId, limit: 50,
    ...(beforeId ? { before_id: beforeId } : {}),
    ...(afterId ? { after_id: afterId } : {}),
  }) // resolves to { messages, has_more }
}

export async function createGroup(name) {
  const res = await authFetch('/api/groups', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
  return res.json()
}

export async function fetchPins(groupId) {
  return wsrpc.request({ query: 'pins', group_id: groupId })
}

export async function pinMessage(groupId, msgId) {
  wsrpc.send({ type: 'mutate', action: 'pin', group_id: groupId, msg_id: msgId })
}

export async function unpinMessage(groupId, msgId) {
  wsrpc.send({ type: 'mutate', action: 'unpin', group_id: groupId, msg_id: msgId })
}

export async function fetchConfig() {
  const res = await authFetch('/api/config')
  return res.json()
}

export async function saveConfig(data) {
  const res = await authFetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.json()
}

export async function uploadFile(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await authFetch('/api/upload', { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || '上传失败')
  }
  return res.json()
}

export async function fetchGroupStats(groupId) {
  const res = await authFetch(`/api/groups/${groupId}/stats`)
  return res.json()
}

export async function updateMember(memberId, data) {
  const res = await authFetch(`/api/members/${memberId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.json()
}

export function exportGroupUrl(groupId, format = 'markdown') {
  return `/api/groups/${groupId}/export?format=${format}`
}

export async function addMember(groupId, payload, type = 'human', role = null, system_prompt = null, avatar_color = '#f59e0b', model_provider = 'deepseek', model_name = 'deepseek-chat') {
  let bodyObj = {};
  if (typeof payload === 'string') {
    bodyObj = {
      name: payload,
      type,
      role,
      system_prompt,
      avatar_color,
      model_provider,
      model_name
    };
  } else {
    bodyObj = payload;
  }
  const res = await authFetch(`/api/groups/${groupId}/members`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(bodyObj)
  })
  return res.json()
}

export async function resumeSession(sessionId) {
  const res = await authFetch(`/api/sessions/${sessionId}/resume`, { method: 'POST' })
  return res.json()
}

export async function cancelSessionRecovery(sessionId) {
  const res = await authFetch(`/api/sessions/${sessionId}/cancel-recovery`, { method: 'POST' })
  return res.json()
}


export async function login(username, password) {
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  })
  if (!res.ok) throw new Error('登录失败')
  return res.json()
}

export async function register(username, password, email) {
  const res = await fetch('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, email })
  })
  if (!res.ok) throw new Error('注册失败')
  return res.json()
}
