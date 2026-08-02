import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'

import TimelinePanel from './TimelinePanel'
import { fetchGroupTimeline } from '../api'


vi.mock('../api', () => ({ fetchGroupTimeline: vi.fn() }))

const workflowItem = {
  event_id: 'evt_workflow',
  occurred_at: 1785664805000,
  source: 'workflow',
  event_type: 'stage_entered',
  context: { group_id: 7, workflow_id: 'wf_7', stage_id: 'build', session_id: 's_7' },
  payload: {},
  policy: { effects: ['control_flow'], business_significant: true },
}

const permissionItem = {
  event_id: 'evt_permission',
  occurred_at: 1785664804000,
  source: 'permission',
  event_type: 'permission_requested',
  context: { group_id: 7, session_id: 's_7', permission_id: 'perm_7' },
  payload: { tool_name: 'write_file', decision_source: 'human_required' },
  policy: { effects: ['authorization'], business_significant: true },
}

describe('TimelinePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchGroupTimeline.mockResolvedValue({
      items: [workflowItem, permissionItem], next_cursor: 'next-page', has_more: true,
    })
  })

  it('renders unified workflow and permission events', async () => {
    render(<TimelinePanel groupId={7} onClose={() => {}} />)
    expect(await screen.findByText('进入阶段')).toBeInTheDocument()
    expect(screen.getByText('请求操作权限')).toBeInTheDocument()
    expect(fetchGroupTimeline).toHaveBeenCalledWith(7, expect.objectContaining({
      businessSignificant: true, sources: [],
    }))
  })

  it('changes source and diagnostic filters', async () => {
    render(<TimelinePanel groupId={7} onClose={() => {}} />)
    await screen.findByText('进入阶段')
    fireEvent.click(screen.getByRole('button', { name: '权限' }))
    await waitFor(() => expect(fetchGroupTimeline).toHaveBeenLastCalledWith(7, expect.objectContaining({ sources: ['permission'] })))
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'diagnostic' } })
    await waitFor(() => expect(fetchGroupTimeline).toHaveBeenLastCalledWith(7, expect.objectContaining({ businessSignificant: false })))
  })

  it('loads older events with the opaque cursor', async () => {
    fetchGroupTimeline
      .mockResolvedValueOnce({ items: [workflowItem], next_cursor: 'next-page', has_more: true })
      .mockResolvedValueOnce({ items: [permissionItem], next_cursor: null, has_more: false })
    render(<TimelinePanel groupId={7} onClose={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: '加载更早事件' }))
    await waitFor(() => expect(fetchGroupTimeline).toHaveBeenLastCalledWith(7, expect.objectContaining({ cursor: 'next-page' })))
    expect(await screen.findByText('请求操作权限')).toBeInTheDocument()
  })

  it('expands correlation IDs and payload details', async () => {
    render(<TimelinePanel groupId={7} onClose={() => {}} />)
    fireEvent.click(await screen.findByText('请求操作权限'))
    expect(await screen.findByText('perm_7')).toBeInTheDocument()
    expect(screen.getAllByText(/write_file/).length).toBeGreaterThan(0)
  })
})
