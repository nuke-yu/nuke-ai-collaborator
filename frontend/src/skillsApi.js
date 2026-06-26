import { authFetch } from './api'

async function jsonOrThrow(res) {
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail || detail } catch { /* non-json */ }
    throw new Error(detail)
  }
  return res.json()
}

export async function fetchScopeSkills(scope) {
  return jsonOrThrow(await authFetch(`/api/skills?scope=${encodeURIComponent(scope)}`))
}

export async function fetchSkillContent(scope, name) {
  return jsonOrThrow(await authFetch(
    `/api/skills/content?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`))
}

export async function writeScopeSkill(scope, name, content) {
  return jsonOrThrow(await authFetch('/api/skills', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scope, name, content }),
  }))
}

export async function deleteScopeSkill(scope, name) {
  return jsonOrThrow(await authFetch(
    `/api/skills?scope=${encodeURIComponent(scope)}&name=${encodeURIComponent(name)}`,
    { method: 'DELETE' }))
}

export async function copyScopeSkill(src, name, dst) {
  return jsonOrThrow(await authFetch('/api/skills/copy', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src, name, dst }),
  }))
}

export async function fetchTemplateRoles(lang = 'zh') {
  return jsonOrThrow(await authFetch(`/api/templates/roles?lang=${encodeURIComponent(lang)}`))
}

export async function fetchGroupRoles(groupId) {
  return jsonOrThrow(await authFetch(`/api/groups/${groupId}/roles`))
}
