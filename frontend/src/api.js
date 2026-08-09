import * as wsrpc from './wsrpc'

export function getApiUrl(url) {
  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url
  }
  if (typeof window !== 'undefined' && (window.location.protocol === 'file:' || !window.location.host)) {
    return `http://127.0.0.1:8000${url.startsWith('/') ? url : `/${url}`}`
  }
  return url
}

export async function authFetch(url, options = {}) {
  const token = localStorage.getItem('token')
  const headers = { ...options.headers }
  if (token) {
    headers['Authorization'] = 'Bearer ' + token
  }
  const fullUrl = getApiUrl(url)
  const res = await fetch(fullUrl, { ...options, headers })
  if (res.status === 401 && !url.includes('/api/auth/')) {
    localStorage.removeItem('token')
    throw new Error('登录凭证已失效，请重新登录')
  }
  if (!res.ok) {
    const errorText = await res.text().catch(() => '')
    try {
      const errorJson = JSON.parse(errorText)
      throw new Error(errorJson.detail || errorJson.message || `请求失败 (${res.status})`)
    } catch (e) {
      if (e.message && !e.message.startsWith('Unexpected token')) {
        throw e
      }
      throw new Error(`服务器响应异常 (${res.status}): ${errorText.slice(0, 100)}`)
    }
  }
  return res
}
export async function fetchAllGroups() {
  const res = await authFetch('/api/groups')
  return res.json()
}

export async function fetchGroupInfo(groupId, { signal } = {}) {
  const res = await authFetch(`/api/groups/${groupId}`, { signal })
  return res.json()
}

export async function fetchReactions(groupId, { signal } = {}) {
  return wsrpc.request({ query: 'reactions', group_id: groupId }, { signal })
}

export async function fetchSessionTimeline(sessionId, groupId, { signal } = {}) {
  const res = await authFetch(`/api/sessions/${sessionId}/timeline?group_id=${groupId}`, { signal })
  return res.json()
}

export async function fetchGroupArtifacts(groupId, { origin, sessionId, botId, limit = 100, offset = 0 } = {}, { signal } = {}) {
  const params = new URLSearchParams()
  if (origin) params.append('origin', origin)
  if (sessionId) params.append('session_id', sessionId)
  if (botId) params.append('bot_id', botId)
  params.append('limit', limit)
  params.append('offset', offset)
  const res = await authFetch(`/api/artifacts/group/${groupId}?${params.toString()}`, { signal })
  return res.json()
}

export async function fetchPersonalMemory({ signal } = {}) {
  const res = await authFetch('/api/personal/memory', { signal })
  return res.json()
}

export async function createPersonalRecord(body) {
  const res = await authFetch('/api/personal/memory/records', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function createPersonalProjection(body) {
  const res = await authFetch('/api/personal/memory/projections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.json()
}

export async function deletePersonalRecord(recordId) {
  const res = await authFetch(`/api/personal/memory/records/${recordId}`, {
    method: 'DELETE',
  })
  return res.json()
}

export async function fetchPersonalRecordImpact(recordId, { signal } = {}) {
  const res = await authFetch(`/api/personal/memory/records/${recordId}/impact`, { signal })
  return res.json()
}

export async function revokePersonalProjection(projectionId) {
  const res = await authFetch(`/api/personal/memory/projections/${projectionId}`, {
    method: 'DELETE',
  })
  return res.json()
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

export async function fetchMessages(groupId, { beforeId, afterId, signal } = {}) {
  return wsrpc.request({
    query: 'messages', group_id: groupId, limit: 50,
    ...(beforeId ? { before_id: beforeId } : {}),
    ...(afterId ? { after_id: afterId } : {}),
  }, { signal }) // resolves to { messages, has_more }
}

export async function createGroup(name) {
  const res = await authFetch('/api/groups', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name })
  })
  return res.json()
}

export async function deleteGroup(groupId) {
  const res = await authFetch(`/api/groups/${groupId}`, { method: 'DELETE' })
  return res.json()
}

export async function fetchPins(groupId, { signal } = {}) {
  return wsrpc.request({ query: 'pins', group_id: groupId }, { signal })
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

export async function uploadFile(file, groupId) {
  const form = new FormData()
  form.append('file', file)
  form.append('group_id', groupId)
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
  let bodyObj;
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

export async function resumeSession(sessionId, groupId) {
  const query = groupId ? `?group_id=${groupId}` : ''
  const res = await authFetch(`/api/sessions/${sessionId}/resume${query}`, { method: 'POST' })
  return res.json()
}

export async function cancelSessionRecovery(sessionId, groupId) {
  const query = groupId ? `?group_id=${groupId}` : ''
  const res = await authFetch(`/api/sessions/${sessionId}/cancel-recovery${query}`, { method: 'POST' })
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

export async function fetchGroupRecap(groupId) {
  const res = await authFetch(`/api/groups/${groupId}/recap`)
  return res.json()
}

// 方案 1：按需 per-user recap —— 概括「我」未确认的新活动。
export async function fetchPersonalRecap(groupId, memberId) {
  const res = await authFetch(`/api/groups/${groupId}/recap/personal/${memberId}`)
  return res.json()
}

// 点 ✕：把「我」的 recap 水位线推进到「这条 recap 实际覆盖到的最新消息 id」（coveredThroughId），
// 而不是点击时刻的 MAX(id)——否则横幅停留期间到达的新消息会被一并吞掉（TOCTOU）。仅清自己的。
export async function ackPersonalRecap(groupId, memberId, coveredThroughId) {
  const opts = { method: 'POST' }
  if (coveredThroughId != null) {
    opts.headers = { 'Content-Type': 'application/json' }
    opts.body = JSON.stringify({ covered_through_id: coveredThroughId })
  }
  const res = await authFetch(`/api/groups/${groupId}/recap/ack/${memberId}`, opts)
  return res.json()
}

export async function fetchAiSuggestions(groupId, awaitingConfirm) {
  const res = await authFetch(`/api/groups/${groupId}/suggest`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ awaiting_confirm: awaitingConfirm })
  })
  return res.json()
}

export async function fetchMcpConfig() {
  const res = await authFetch('/api/config/mcp')
  if (!res.ok) {
    let detail = 'Failed to load MCP config'
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  const etag = res.headers.get('ETag')
  const config = await res.json()
  return { config, etag }
}

export async function saveMcpConfig(data, etag) {
  const headers = { 'Content-Type': 'application/json' }
  if (etag) headers['If-Match'] = etag
  const res = await authFetch('/api/config/mcp', {
    method: 'PUT',
    headers,
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    let detail = 'Failed to save MCP config'
    try { detail = (await res.json()).detail || detail } catch { /* ignore */ }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  const newEtag = res.headers.get('ETag')
  const config = await res.json()
  return { config, etag: newEtag }
}

export async function fetchGroupTimeline(groupId, options = {}) {
  const params = new URLSearchParams()
  if (options.limit) params.set('limit', String(options.limit))
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.businessSignificant !== undefined) {
    params.set('business_significant', String(options.businessSignificant))
  }
  for (const source of options.sources || []) params.append('source', source)
  for (const eventType of options.eventTypes || []) params.append('event_type', eventType)
  for (const eventClass of options.eventClasses || []) params.append('event_class', eventClass)
  if (options.workflowId) params.set('workflow_id', options.workflowId)
  if (options.sessionId) params.set('session_id', options.sessionId)
  const query = params.toString()
  const res = await authFetch(`/api/groups/${groupId}/timeline${query ? `?${query}` : ''}`)
  return res.json()
}
