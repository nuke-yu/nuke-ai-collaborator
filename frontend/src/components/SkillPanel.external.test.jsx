import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import SkillPanel from './SkillPanel'

vi.mock('../skillsApi', () => ({
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [] })),
  copyScopeSkill: vi.fn(),
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
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ skills: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('opens the external-skills panel from the header button', async () => {
    render(<SkillPanel bot={{ id: 3, name: 'dev', role: 'developer' }} groupId={7} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('open-external-skills'))
    const { fetchMemberExternalSkills } = await import('../externalSkillsApi')
    await waitFor(() => expect(fetchMemberExternalSkills).toHaveBeenCalledWith(7, 3))
  })
})
