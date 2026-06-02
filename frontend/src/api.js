
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
  const res = await authFetch(`/api/groups/${groupId}/reactions`)
  return res.json()
}

export async function toggleReaction(msgId, memberId, emoji) {
  await authFetch(`/api/messages/${msgId}/reactions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ member_id: memberId, emoji }),
  })
}

export async function searchMessages(groupId, q) {
  const res = await authFetch(`/api/groups/${groupId}/messages/search?q=${encodeURIComponent(q)}`)
  return res.json()
}

export async function fetchUnreadCounts(memberId) {
  const res = await authFetch(`/api/members/${memberId}/unread`)
  return res.json()
}

export async function fetchMessages(groupId, { beforeId } = {}) {
  const params = new URLSearchParams({ limit: 50 })
  if (beforeId) params.set('before_id', beforeId)
  const res = await authFetch(`/api/groups/${groupId}/messages?${params}`)
  return res.json()
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
  const res = await authFetch(`/api/groups/${groupId}/pins`)
  return res.json()
}

export async function pinMessage(groupId, msgId) {
  await authFetch(`/api/groups/${groupId}/messages/${msgId}/pin`, { method: 'POST' })
}

export async function unpinMessage(groupId, msgId) {
  await authFetch(`/api/groups/${groupId}/messages/${msgId}/pin`, { method: 'DELETE' })
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
