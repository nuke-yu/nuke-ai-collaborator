import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  fetchScopeSkills, copyScopeSkill, fetchTemplateRoles, fetchGroupRoles,
} from './skillsApi'

describe('skillsApi wiring', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'tok', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ skills: [], roles: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('fetchScopeSkills encodes the scope descriptor', async () => {
    await fetchScopeSkills('role:7:PM')
    const [url] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/skills?scope=role%3A7%3APM')
  })

  it('copyScopeSkill posts src/name/dst', async () => {
    await copyScopeSkill('template:zh:PM', 'write-spec', 'role:7:PM')
    const [url, opts] = global.fetch.mock.calls[0]
    expect(url).toBe('/api/skills/copy')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ src: 'template:zh:PM', name: 'write-spec', dst: 'role:7:PM' })
  })

  it('fetchTemplateRoles passes lang', async () => {
    await fetchTemplateRoles('en')
    expect(global.fetch.mock.calls[0][0]).toBe('/api/templates/roles?lang=en')
  })

  it('fetchGroupRoles hits the group path with default lang zh', async () => {
    await fetchGroupRoles(7)
    expect(global.fetch.mock.calls[0][0]).toBe('/api/groups/7/roles?lang=zh')
  })

  it('fetchGroupRoles normalizes locale to en/zh', async () => {
    await fetchGroupRoles(7, 'en-US')
    expect(global.fetch.mock.calls[0][0]).toBe('/api/groups/7/roles?lang=en')
  })
})
