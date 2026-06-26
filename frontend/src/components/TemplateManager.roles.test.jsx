// frontend/src/components/TemplateManager.roles.test.jsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import TemplateManager from './TemplateManager'

vi.mock('../skillsApi', () => ({
  fetchTemplateRoles: vi.fn(() => Promise.resolve({
    lang: 'zh',
    roles: [{ role: 'PM', display_name: '需求分析师', avatar_color: '#0ea5e9', system_prompt: '', skill_count: 3 }],
  })),
  fetchScopeSkills: vi.fn(() => Promise.resolve({ skills: [] })),
}))
// keep the legacy DB template fetch from blowing up
// TemplateManager uses bare fetch() for legacy DB templates (not authFetch);
// we mock global fetch so the /api/templates call doesn't crash in jsdom.
// We also mock ../api so skillsApi's transitive authFetch import resolves cleanly.
vi.mock('../api', () => ({
  authFetch: vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })),
}))

describe('TemplateManager role catalog', () => {
  beforeEach(() => {
    global.localStorage = { getItem: () => 'zh', setItem: () => {}, removeItem: () => {} }
    // mock bare fetch used by legacy loadTemplates()
    global.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve([]) }))
  })
  afterEach(() => vi.restoreAllMocks())

  it('lists file-based template roles', async () => {
    render(<TemplateManager onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/需求分析师/)).toBeInTheDocument())
  })
})
