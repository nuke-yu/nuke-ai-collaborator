import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import '@testing-library/jest-dom'
import MemberList from './MemberList'

vi.mock('../skillsApi', () => ({
  fetchGroupRoles: vi.fn(() => Promise.resolve({
    roles: [
      { role: 'PM', display_name: '需求分析师', avatar_color: '#0ea5e9', system_prompt: '你是需求分析师', skill_count: 3 },
      { role: '系统架构师', display_name: '系统架构师', avatar_color: '#8b5cf6', system_prompt: '', skill_count: 2 },
    ],
  })),
}))

describe('MemberList role dropdown', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
  })
  afterEach(() => vi.restoreAllMocks())

  it('renders a role option per catalog entry for a bot in a provisioned group', async () => {
    render(<MemberList groupId={7} onAddMember={() => {}} onClose={() => {}}
                       initialData={{ type: 'bot' }} />)
    // ThemedSelect is a custom dropdown: options render only once the menu opens.
    // Locate the trigger by its placeholder text (a combobox's text content is
    // its value, not its accessible name, so role+name can't find it).
    const trigger = (await screen.findByText(/选择角色/)).closest('button')
    fireEvent.click(trigger)
    await waitFor(() => {
      expect(screen.getByRole('option', { name: /需求分析师/ })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: /系统架构师/ })).toBeInTheDocument()
    })
  })
})
