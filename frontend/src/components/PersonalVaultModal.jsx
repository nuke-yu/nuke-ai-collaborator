import { useEffect, useState } from 'react'
import {
  fetchPersonalMemory,
  createPersonalRecord,
  createPersonalProjection,
  deletePersonalRecord,
  revokePersonalProjection,
} from '../api'

export default function PersonalVaultModal({ groups = [], onClose }) {
  const [tab, setTab] = useState('records') // 'records' | 'projections'
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [vaultData, setVaultData] = useState({ records: [], projections: [] })

  // Form states
  const [newKind, setNewKind] = useState('preference')
  const [newContent, setNewContent] = useState('')
  const [newSensitivity, setNewSensitivity] = useState('private')
  const [submittingRecord, setSubmittingRecord] = useState(false)

  const [selectedRecordId, setSelectedRecordId] = useState('')
  const [targetGroupId, setTargetGroupId] = useState(groups[0]?.id || '')
  const [projPurpose, setProjPurpose] = useState('assistant_context')
  const [submittingProjection, setSubmittingProjection] = useState(false)

  const loadVault = async () => {
    try {
      setLoading(true)
      setError(null)
      const data = await fetchPersonalMemory()
      setVaultData(data || { records: [], projections: [] })
      if (data?.records?.length > 0 && !selectedRecordId) {
        setSelectedRecordId(data.records[0].record_id)
      }
    } catch (err) {
      setError(err.message || '加载个人知识库失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadVault()
  }, [])

  const handleAddRecord = async (e) => {
    e.preventDefault()
    if (!newContent.trim()) return
    try {
      setSubmittingRecord(true)
      await createPersonalRecord({
        kind: newKind,
        content: newContent.trim(),
        sensitivity: newSensitivity,
        source_type: 'manual',
      })
      setNewContent('')
      await loadVault()
    } catch (err) {
      alert(err.message || '添加个人记录失败')
    } finally {
      setSubmittingRecord(false)
    }
  }

  const handleDeleteRecord = async (recordId) => {
    if (!confirm('确定要删除该条个人事实/偏好记录吗？（关联的授权也将被一并撤销）')) return
    try {
      await deletePersonalRecord(recordId)
      await loadVault()
    } catch (err) {
      alert(err.message || '删除记录失败')
    }
  }

  const handleAddProjection = async (e) => {
    e.preventDefault()
    if (!selectedRecordId || !targetGroupId) return
    try {
      setSubmittingProjection(true)
      await createPersonalProjection({
        record_id: selectedRecordId,
        group_id: Number(targetGroupId),
        purpose: projPurpose,
      })
      await loadVault()
    } catch (err) {
      alert(err.message || '创建群组授权失败')
    } finally {
      setSubmittingProjection(false)
    }
  }

  const handleRevokeProjection = async (projId) => {
    if (!confirm('确定要撤销对该群组的记忆授权吗？')) return
    try {
      await revokePersonalProjection(projId)
      await loadVault()
    } catch (err) {
      alert(err.message || '撤销授权失败')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 animate-fade-in">
      <div className="relative w-full max-w-3xl max-h-[85vh] bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden animate-scale-up">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 bg-gray-900/90 backdrop-blur-md">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🧠</span>
            <div>
              <h2 className="font-semibold text-gray-100 text-base">个人知识库控制台 (Personal Vault)</h2>
              <p className="text-xs text-gray-400">隔离管理个人事实、偏好与跨 Group 记忆授信边界</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Tab Buttons */}
        <div className="flex border-b border-gray-800 bg-gray-950/60 px-6 pt-2 gap-4">
          <button
            type="button"
            onClick={() => setTab('records')}
            className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors ${
              tab === 'records'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-gray-400 hover:text-gray-200'
            }`}
          >
            🧠 个人事实与偏好 ({vaultData.records?.length || 0})
          </button>
          <button
            type="button"
            onClick={() => setTab('projections')}
            className={`pb-2.5 text-xs font-semibold border-b-2 transition-colors ${
              tab === 'projections'
                ? 'border-indigo-500 text-indigo-400'
                : 'border-transparent text-gray-400 hover:text-gray-200'
            }`}
          >
            👥 群组授信管理 ({vaultData.projections?.length || 0})
          </button>
        </div>

        {/* Main Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {loading && (
            <div className="flex items-center justify-center py-12 text-gray-400 gap-2">
              <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-xs">加载 Personal Vault 数据中...</span>
            </div>
          )}

          {error && (
            <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
              {error}
            </div>
          )}

          {!loading && tab === 'records' && (
            <div className="space-y-6">
              {/* Add New Record Form */}
              <form onSubmit={handleAddRecord} className="p-4 rounded-xl border border-gray-800 bg-gray-850/60 space-y-3">
                <h3 className="text-xs font-semibold text-gray-200">➕ 手动录入个人事实 / 偏好</h3>
                <div className="flex gap-3">
                  <select
                    value={newKind}
                    onChange={(e) => setNewKind(e.target.value)}
                    className="bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-2.5 py-2 outline-none focus:ring-1 focus:ring-indigo-500"
                  >
                    <option value="preference">偏好 (Preference)</option>
                    <option value="profile">个人资料 (Profile)</option>
                    <option value="expertise">专业技能 (Expertise)</option>
                    <option value="decision">项目决策 (Decision)</option>
                    <option value="habit">工作习惯 (Habit)</option>
                  </select>
                  <input
                    type="text"
                    placeholder="输入个人事实，例如：更偏好 TypeScript 而非 Python 开发 UI 界面"
                    value={newContent}
                    onChange={(e) => setNewContent(e.target.value)}
                    className="flex-1 bg-gray-800 border border-gray-700 text-gray-100 text-xs rounded-lg px-3 py-2 outline-none focus:ring-1 focus:ring-indigo-500"
                  />
                  <button
                    type="submit"
                    disabled={submittingRecord || !newContent.trim()}
                    className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
                  >
                    {submittingRecord ? '保存中...' : '录入'}
                  </button>
                </div>
              </form>

              {/* Records List */}
              <div className="space-y-3">
                {vaultData.records.length === 0 ? (
                  <div className="text-center py-8 text-gray-500 text-xs">
                    暂无个人知识条目
                  </div>
                ) : (
                  vaultData.records.map((r) => (
                    <div key={r.record_id} className="flex items-center justify-between p-3.5 rounded-xl border border-gray-800 bg-gray-850 hover:border-gray-700 transition-colors">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="px-2 py-0.5 rounded-md text-[10px] font-medium bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
                            {r.kind}
                          </span>
                          <span className="px-2 py-0.5 rounded-md text-[10px] font-medium bg-gray-700 text-gray-300">
                            {r.explicit ? '👤 用户录入' : '🤖 系统推断'}
                          </span>
                          <span className="text-[10px] text-gray-500 font-mono">
                            置信度: {(r.confidence * 100).toFixed(0)}%
                          </span>
                        </div>
                        <p className="text-xs text-gray-200">{r.content}</p>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleDeleteRecord(r.record_id)}
                        className="px-2.5 py-1 text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded-lg transition-colors"
                      >
                        删除
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {!loading && tab === 'projections' && (
            <div className="space-y-6">
              {/* Add New Projection Form */}
              <form onSubmit={handleAddProjection} className="p-4 rounded-xl border border-gray-800 bg-gray-850/60 space-y-3">
                <h3 className="text-xs font-semibold text-gray-200">🔐 新增 Group / Bot 记忆授权 (Scoped Projection)</h3>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">选择个人事实/条目</label>
                    <select
                      value={selectedRecordId}
                      onChange={(e) => setSelectedRecordId(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-2.5 py-2 outline-none focus:ring-1 focus:ring-indigo-500"
                    >
                      {vaultData.records.map((r) => (
                        <option key={r.record_id} value={r.record_id}>
                          [{r.kind}] {r.content.slice(0, 30)}...
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-[10px] text-gray-400 mb-1">授权目标 Group</label>
                    <select
                      value={targetGroupId}
                      onChange={(e) => setTargetGroupId(e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 text-gray-200 text-xs rounded-lg px-2.5 py-2 outline-none focus:ring-1 focus:ring-indigo-500"
                    >
                      {groups.map((g) => (
                        <option key={g.id} value={g.id}>
                          # {g.name} (ID: {g.id})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="flex items-end">
                    <button
                      type="submit"
                      disabled={submittingProjection || !selectedRecordId || !targetGroupId}
                      className="w-full py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors disabled:opacity-50"
                    >
                      {submittingProjection ? '授权中...' : '允许跨 Group 共享'}
                    </button>
                  </div>
                </div>
              </form>

              {/* Projections List */}
              <div className="space-y-3">
                {vaultData.projections.length === 0 ? (
                  <div className="text-center py-8 text-gray-500 text-xs">
                    暂无活性 Group 授信记录
                  </div>
                ) : (
                  vaultData.projections.map((p) => (
                    <div key={p.projection_id} className="flex items-center justify-between p-3.5 rounded-xl border border-gray-800 bg-gray-850 hover:border-gray-700 transition-colors">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="px-2 py-0.5 rounded-md text-[10px] font-medium bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                            Group ID: {p.group_id}
                          </span>
                          <span className="text-[10px] text-gray-400 font-mono">
                            Record: {p.record_id}
                          </span>
                        </div>
                        <p className="text-xs text-gray-400 font-mono">Purpose: {p.purpose}</p>
                      </div>

                      <button
                        type="button"
                        onClick={() => handleRevokeProjection(p.projection_id)}
                        className="px-2.5 py-1 text-xs text-rose-400 hover:text-rose-300 hover:bg-rose-500/10 rounded-lg transition-colors"
                      >
                        撤回授权
                      </button>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
