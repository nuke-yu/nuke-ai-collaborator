import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import ExternalSkillPanel from './ExternalSkillPanel'

vi.mock('../externalSkillsApi', () => ({
  fetchMemberExternalSkills: vi.fn(),
  putMemberExternalSkills: vi.fn(() => Promise.resolve({ assigned: [] })),
  importExternalSkill: vi.fn(),
  removeExternalSkill: vi.fn(),
  fetchPermissionRules: vi.fn(() => Promise.resolve([])),
  addPermissionRule: vi.fn(),
  removePermissionRule: vi.fn(),
}))

const POOL = [
  { id: 1, name: 'deploy', scope_kind: 'global', group_id: 0, source_url: 'https://github.com/x/y',
    version: '1.2.0', platforms: 'pure', high_privilege: '', imported_by: null, imported_at: '2026-06-27', status: 'active' },
  { id: 2, name: 'nuke-prod', scope_kind: 'group', group_id: 7, source_url: 'https://github.com/x/z',
    version: '', platforms: 'posix', high_privilege: 'run_shell', imported_by: 42, imported_at: '2026-06-27', status: 'active' },
]

describe('ExternalSkillPanel — pool + assignment', () => {
  let api
  beforeEach(async () => {
    vi.clearAllMocks()
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({
      pool: POOL,
      assigned: [{ skill_name: 'deploy', pool: 'external_global', enabled: true, assigned_by: null }],
    })
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders pool rows with badges and high-privilege warning', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    expect(screen.getByText('nuke-prod')).toBeInTheDocument()
    // high-privilege warning shows the tool name
    expect(screen.getByText(/run_shell/)).toBeInTheDocument()
  })

  it('toggling an unassigned skill PUTs the full desired set', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('nuke-prod')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('assign-toggle-nuke-prod'))
    await waitFor(() => expect(api.putMemberExternalSkills).toHaveBeenCalled())
    const [gid, botId, assigned] = api.putMemberExternalSkills.mock.calls[0]
    expect(gid).toBe(7)
    expect(botId).toBe(3)
    const names = assigned.map(a => a.name).sort()
    expect(names).toEqual(['deploy', 'nuke-prod'])           // deploy kept, nuke-prod added
    const np = assigned.find(a => a.name === 'nuke-prod')
    expect(np.pool).toBe('external_group')                    // scope_kind:group -> external_group
    expect(np.enabled).toBe(true)
  })

  it('toggling off an assigned skill removes it from the desired set', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('assign-toggle-deploy'))
    await waitFor(() => expect(api.putMemberExternalSkills).toHaveBeenCalled())
    const assigned = api.putMemberExternalSkills.mock.calls[0][2]
    expect(assigned.map(a => a.name)).toEqual([])             // deploy removed, nothing else assigned
  })
})
