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
    version: '1.2.0', platforms: 'pure', high_privilege: '', imported_by: null, imported_at: '2026-06-27', status: 'active',
    description: 'Ship the build to prod' },
  { id: 2, name: 'nuke-prod', scope_kind: 'group', group_id: 7, source_url: 'https://github.com/x/z',
    version: '', platforms: 'posix', high_privilege: 'run_shell', imported_by: 42, imported_at: '2026-06-27', status: 'active',
    description: 'Wipe and rebuild the prod cluster' },
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
    // description (derived from SKILL.md at GET time) renders
    expect(screen.getByText('Ship the build to prod')).toBeInTheDocument()
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

describe('ExternalSkillPanel — import', () => {
  let api
  beforeEach(async () => {
    vi.clearAllMocks()
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: [], assigned: [] })
    api.importExternalSkill.mockResolvedValue({ imported: [{ id: 9, name: 'new-skill' }], rejected: [] })
  })
  afterEach(() => vi.restoreAllMocks())

  it('submits the import form with group scope and reloads the pool', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByTestId('open-import'))
    fireEvent.change(screen.getByTestId('import-url'), { target: { value: 'https://github.com/x/y' } })
    fireEvent.change(screen.getByTestId('import-ref'), { target: { value: 'v1' } })
    // scope select defaults to 'group'
    fireEvent.click(screen.getByTestId('submit-import'))

    await waitFor(() => expect(api.importExternalSkill).toHaveBeenCalled())
    expect(api.importExternalSkill).toHaveBeenCalledWith({
      git_url: 'https://github.com/x/y', ref: 'v1', scope: { group_id: 7 },
    })
    // pool reloaded after import
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(2))
  })

  it('sends scope:"global" when global is selected', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByTestId('open-import'))
    fireEvent.change(screen.getByTestId('import-scope'), { target: { value: 'global' } })
    fireEvent.change(screen.getByTestId('import-url'), { target: { value: 'https://github.com/a/b' } })
    fireEvent.click(screen.getByTestId('submit-import'))
    await waitFor(() => expect(api.importExternalSkill).toHaveBeenCalled())
    expect(api.importExternalSkill.mock.calls[0][0].scope).toBe('global')
  })
})

describe('ExternalSkillPanel — remove', () => {
  let api
  beforeEach(async () => {
    vi.clearAllMocks()
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: POOL, assigned: [] })
    api.removeExternalSkill.mockResolvedValue({ id: 1 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })
  afterEach(() => vi.restoreAllMocks())

  it('removes a pool skill after confirm and reloads', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('remove-skill-1'))
    await waitFor(() => expect(api.removeExternalSkill).toHaveBeenCalledWith(1))
    await waitFor(() => expect(api.fetchMemberExternalSkills).toHaveBeenCalledTimes(2))
  })

  it('does nothing when confirm is cancelled', async () => {
    window.confirm.mockReturnValue(false)
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('remove-skill-1'))
    expect(api.removeExternalSkill).not.toHaveBeenCalled()
  })
})

describe('ExternalSkillPanel — approval policy', () => {
  let api
  beforeEach(async () => {
    vi.clearAllMocks()
    api = await import('../externalSkillsApi')
    api.fetchMemberExternalSkills.mockResolvedValue({ pool: POOL, assigned: [] })
    // 'nuke-prod' is high-privilege; start with no rules
    api.fetchPermissionRules.mockResolvedValue([])
    api.addPermissionRule.mockResolvedValue({ id: 11 })
    api.removePermissionRule.mockResolvedValue({})
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows a policy dropdown only for high-privilege skills', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('nuke-prod')).toBeInTheDocument())
    expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument()
    expect(screen.queryByTestId('policy-deploy')).not.toBeInTheDocument()   // deploy has no high_privilege
  })

  it('selecting Deny posts a name-scoped deny rule', async () => {
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('policy-nuke-prod'), { target: { value: 'deny' } })
    await waitFor(() => expect(api.addPermissionRule).toHaveBeenCalled())
    expect(api.addPermissionRule).toHaveBeenCalledWith(3, {
      tool_pattern: 'run_skill', args_pattern: 'nuke-prod', action: 'deny',
    })
  })

  it('selecting Ask deletes the existing matching rule', async () => {
    api.fetchPermissionRules.mockResolvedValue([
      { id: 88, tool_pattern: 'run_skill', args_pattern: 'nuke-prod', action: 'allow' },
    ])
    render(<ExternalSkillPanel bot={{ id: 3, name: 'dev' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('policy-nuke-prod')).toBeInTheDocument())
    fireEvent.change(screen.getByTestId('policy-nuke-prod'), { target: { value: 'ask' } })
    await waitFor(() => expect(api.removePermissionRule).toHaveBeenCalledWith(3, 88))
  })
})
