import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import SkillPanel from './SkillPanel'

vi.mock('../skillsApi', () => ({
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [] })),
  copyScopeSkill: vi.fn(),
  fetchSkillContent: vi.fn(() => Promise.resolve({ content: 'mock content' })),
  writeScopeSkill: vi.fn(() => Promise.resolve({ name: 'test' })),
}))
vi.mock('../externalSkillsApi', () => ({
  fetchMemberExternalSkills: vi.fn(() => Promise.resolve({ pool: [], assigned: [] })),
  putMemberExternalSkills: vi.fn(),
  importExternalSkill: vi.fn(),
  removeExternalSkill: vi.fn(),
  fetchPermissionRules: vi.fn(() => Promise.resolve([])),
  addPermissionRule: vi.fn(),
  removePermissionRule: vi.fn(),
}))

describe('SkillPanel external-skills entry point', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        skills: [
          { name: 'git-helper', layer: 'system', description: 'System tool', status: 'active', always: false, injected: 'metadata' },
          { name: 'deploy', layer: 'group', description: 'Deploy tool', status: 'active', always: false, injected: 'metadata' }
        ]
      })
    }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('opens the external-skills panel from the header button', async () => {
    render(<SkillPanel bot={{ id: 3, name: 'dev', role: 'developer' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('git-helper')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('open-external-skills'))
    const { fetchMemberExternalSkills } = await import('../externalSkillsApi')
    await waitFor(() => expect(fetchMemberExternalSkills).toHaveBeenCalledWith(7, 3))
  })

  it('opens skill editor in view-only mode for system skills', async () => {
    const api = await import('../skillsApi')
    render(<SkillPanel bot={{ id: 3, name: 'dev', role: 'developer' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('git-helper')).toBeInTheDocument())
    
    // Find the View/Edit button in the git-helper row
    const buttons = screen.getAllByRole('button', { name: '查看' }) // '查看' is the view translation in zh
    fireEvent.click(buttons[0])

    await waitFor(() => expect(api.fetchSkillContent).toHaveBeenCalledWith('system', 'git-helper'))
    const textarea = screen.getByPlaceholderText('# SKILL.md...')
    expect(textarea).toBeInTheDocument()
    expect(textarea).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument()
  })

  it('opens skill editor in edit mode for group skills and saves content', async () => {
    const api = await import('../skillsApi')
    render(<SkillPanel bot={{ id: 3, name: 'dev', role: 'developer' }} groupId={7} onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText('deploy')).toBeInTheDocument())
    
    const buttons = screen.getAllByRole('button', { name: '编辑' })
    fireEvent.click(buttons[0])

    await waitFor(() => expect(api.fetchSkillContent).toHaveBeenCalledWith('group:7', 'deploy'))
    const textarea = screen.getByPlaceholderText('# SKILL.md...')
    expect(textarea).toBeInTheDocument()
    expect(textarea).not.toHaveAttribute('readonly')

    fireEvent.change(textarea, { target: { value: 'updated deployment guidelines' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(api.writeScopeSkill).toHaveBeenCalledWith('group:7', 'deploy', 'updated deployment guidelines'))
  })
})
