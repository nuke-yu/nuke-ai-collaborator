import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../../i18n/keys'

export default function RequirementPanel({ groupBots = [], onStart, onClose }) {
  const { t } = useTranslation()

  // Mirror backend _role_family() so default selection matches dispatch.py's auto-pipeline
  const getBotByRoleFamily = (family) => {
    return groupBots.find(b => {
      const r = (b.role || '').toLowerCase()
      if (family === 'qa') return ['qa', 'test', 'quality', '测试', '质量'].some(s => r.includes(s))
      if (family === 'ba') return r === 'ba' || ['business', 'analyst', 'product', 'pm', '需求', '产品', '业务'].some(s => r.includes(s))
      if (family === 'dev') return ['dev', 'engineer', 'developer', 'code', 'fullstack', 'full-stack', 'frontend', 'backend', '开发', '工程', '前端', '后端'].some(s => r.includes(s))
      return false
    })
  }

  const [baId, setBaId] = useState('')
  const [devId, setDevId] = useState('')
  const [qaId, setQaId] = useState('')
  const [doneKeyword, setDoneKeyword] = useState('完毕')

  useEffect(() => {
    const defaultBa = getBotByRoleFamily('ba') || groupBots[0]
    const defaultDev = getBotByRoleFamily('dev') || groupBots[Math.min(1, groupBots.length - 1)]
    const defaultQa = getBotByRoleFamily('qa') || groupBots[Math.min(2, groupBots.length - 1)]

    if (defaultBa) setBaId(defaultBa.id)
    if (defaultDev) setDevId(defaultDev.id)
    if (defaultQa) setQaId(defaultQa.id)
  }, [groupBots])

  const handleStart = () => {
    if (!baId || !devId || !qaId) {
      alert('请确保已为所有阶段（BA, Dev, QA）指定参与的 Bot。')
      return
    }

    onStart({
      orchestrator_id: 'workflow_v1',
      stages: [
        { bot_id: parseInt(baId, 10), done_keyword: doneKeyword },
        { bot_id: parseInt(devId, 10), done_keyword: doneKeyword },
        { bot_id: parseInt(qaId, 10), done_keyword: doneKeyword }
      ]
    })
  }

  return (
    <div className="space-y-4 text-left">
      <p className="text-gray-400 text-xs">
        配置经典的软件研发需求流水线（BA需求设计 → Dev编码实现 → QA测试验收）。每步完成后需人类确认方可继续。
      </p>

      <div className="space-y-3">
        {/* BA Select */}
        <div className="space-y-1">
          <label className="text-gray-400 text-xs font-semibold">📋 需求分析阶段 (BA)</label>
          <select
            value={baId}
            onChange={e => setBaId(e.target.value)}
            className="w-full bg-gray-850 text-white text-xs rounded border border-gray-700 px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
          >
            <option value="">-- 选择 BA Bot --</option>
            {groupBots.map(b => (
              <option key={b.id} value={b.id}>{b.name} ({b.role || '无角色'})</option>
            ))}
          </select>
        </div>

        {/* Dev Select */}
        <div className="space-y-1">
          <label className="text-gray-400 text-xs font-semibold">💻 开发实现阶段 (Developer)</label>
          <select
            value={devId}
            onChange={e => setDevId(e.target.value)}
            className="w-full bg-gray-850 text-white text-xs rounded border border-gray-700 px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
          >
            <option value="">-- 选择 Developer Bot --</option>
            {groupBots.map(b => (
              <option key={b.id} value={b.id}>{b.name} ({b.role || '无角色'})</option>
            ))}
          </select>
        </div>

        {/* QA Select */}
        <div className="space-y-1">
          <label className="text-gray-400 text-xs font-semibold">🛡️ 测试验收阶段 (QA)</label>
          <select
            value={qaId}
            onChange={e => setQaId(e.target.value)}
            className="w-full bg-gray-850 text-white text-xs rounded border border-gray-700 px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500 cursor-pointer"
          >
            <option value="">-- 选择 QA Bot --</option>
            {groupBots.map(b => (
              <option key={b.id} value={b.id}>{b.name} ({b.role || '无角色'})</option>
            ))}
          </select>
        </div>

        {/* Done Keyword */}
        <div className="space-y-1">
          <label className="text-gray-400 text-xs font-semibold">🔑 阶段结束标识词 (Done Keyword)</label>
          <input
            type="text"
            value={doneKeyword}
            onChange={e => setDoneKeyword(e.target.value)}
            placeholder="例如: 完毕"
            className="w-full bg-gray-850 text-white text-xs rounded border border-gray-700 px-2.5 py-1.5 outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </div>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          onClick={handleStart}
          className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
        >
          ⚡ {t(K.workflow.start)}
        </button>
        <button
          onClick={onClose}
          className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors"
        >
          {t(K.workflow.cancel)}
        </button>
      </div>
    </div>
  )
}
