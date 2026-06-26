import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import SkillPanel from './SkillPanel'

vi.mock('../skillsApi', () => ({
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [{ name: 'design-architecture' }] })),
  copyScopeSkill: vi.fn(() => Promise.resolve({ ok: true })),
}))

describe('SkillPanel scope browser', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ skills: [] }) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('lists skills from a chosen scope and can copy one', async () => {
    const { fetchScopeSkills, copyScopeSkill } = await import('../skillsApi')
    render(<SkillPanel bot={{ id: 1, role: '系统架构师' }} groupId={7} onClose={() => {}} />)
    fireEvent.click(screen.getByTestId('browse-scopes-toggle'))
    await waitFor(() => expect(fetchScopeSkills).toHaveBeenCalled())
    await waitFor(() => expect(screen.getByText('design-architecture')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('copy-skill-design-architecture'))
    await waitFor(() => expect(copyScopeSkill).toHaveBeenCalled())
  })
})
