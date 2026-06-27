import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

vi.mock('./api', () => ({ authFetch: vi.fn() }))

function ok(body) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
}
function fail(detail, status = 400) {
  return Promise.resolve({ ok: false, status, statusText: 'err', json: () => Promise.resolve({ detail }) })
}

describe('externalSkillsApi', () => {
  let authFetch
  beforeEach(async () => {
    ({ authFetch } = await import('./api'))
    authFetch.mockClear()
  })
  afterEach(() => vi.restoreAllMocks())

  it('fetchMemberExternalSkills GETs the group/member skills route', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ pool: [], assigned: [] }))
    const r = await api.fetchMemberExternalSkills(7, 3)
    expect(authFetch).toHaveBeenCalledWith('/api/groups/7/members/3/skills')
    expect(r).toEqual({ pool: [], assigned: [] })
  })

  it('putMemberExternalSkills PUTs the assigned array', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ assigned: [] }))
    const assigned = [{ name: 'deploy', pool: 'external_global', enabled: true }]
    await api.putMemberExternalSkills(7, 3, assigned)
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/groups/7/members/3/skills')
    expect(opts.method).toBe('PUT')
    expect(JSON.parse(opts.body)).toEqual({ assigned })
  })

  it('importExternalSkill POSTs git_url/ref/scope', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ imported: [], rejected: [] }))
    await api.importExternalSkill({ git_url: 'https://github.com/x/y', ref: 'main', scope: 'global' })
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/skills/import')
    expect(JSON.parse(opts.body)).toEqual({ git_url: 'https://github.com/x/y', ref: 'main', scope: 'global' })
  })

  it('removeExternalSkill DELETEs by id', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ id: 5 }))
    await api.removeExternalSkill(5)
    expect(authFetch).toHaveBeenCalledWith('/api/skills/external/5', { method: 'DELETE' })
  })

  it('addPermissionRule POSTs a name-scoped rule', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({ id: 9 }))
    await api.addPermissionRule(3, { tool_pattern: 'run_skill', args_pattern: 'deploy', action: 'deny' })
    const [url, opts] = authFetch.mock.calls[0]
    expect(url).toBe('/api/members/3/permissions')
    expect(JSON.parse(opts.body)).toEqual({ tool_pattern: 'run_skill', args_pattern: 'deploy', action: 'deny' })
  })

  it('removePermissionRule DELETEs by id', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(ok({}))
    await api.removePermissionRule(3, 12)
    expect(authFetch).toHaveBeenCalledWith('/api/members/3/permissions/12', { method: 'DELETE' })
  })

  it('throws the backend detail on non-ok', async () => {
    const api = await import('./externalSkillsApi')
    authFetch.mockReturnValueOnce(fail('scope must be global or {group_id}'))
    await expect(api.importExternalSkill({ git_url: 'x', ref: '', scope: 'bad' }))
      .rejects.toThrow('scope must be global or {group_id}')
  })
})
