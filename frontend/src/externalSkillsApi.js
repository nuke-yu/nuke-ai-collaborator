import { authFetch } from './api'

async function jsonOrThrow(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-json */ }
    throw new Error(detail)
  }
  return res.json()
}

// --- Capability: per-bot assignment (bot_skills via the groups route) ---

export async function fetchMemberExternalSkills(groupId, botId) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/members/${botId}/skills`))
}

// `assigned` is the FULL desired set: the backend reconciles bot_skills to match
// it exactly, so omitting a skill removes its assignment. Each entry is
// { name, pool, enabled }.
export async function putMemberExternalSkills(groupId, botId, assigned) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/members/${botId}/skills`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ assigned }),
  }))
}

// --- Pool lifecycle: import / remove (external_skills registry) ---

export async function importExternalSkill({ git_url, ref, scope }) {
  return jsonOrThrow(await authFetch('/api/skills/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ git_url, ref, scope }),
  }))
}

export async function removeExternalSkill(id) {
  return jsonOrThrow(await authFetch(`/api/skills/external/${id}`, { method: 'DELETE' }))
}

// --- Security: execution-approval policy (permission_rules) ---
// Separate path from assignment on purpose: capability != permission.

export async function fetchPermissionRules(botId) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions`))
}

export async function addPermissionRule(botId, { tool_pattern, args_pattern, action }) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tool_pattern, args_pattern, action }),
  }))
}

export async function removePermissionRule(botId, ruleId) {
  return jsonOrThrow(await authFetch(`/api/members/${botId}/permissions/${ruleId}`, { method: 'DELETE' }))
}
