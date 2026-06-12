import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import RequirementPanel from './workflow/RequirementPanel'
import DiscussionPanel from './workflow/DiscussionPanel'

export default function WorkflowStartModal({ onStart, onClose, groupBots = [] }) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState('workflow_v1')

  const tabs = [
    { id: 'workflow_v1', name: t(K.workflow.tabPipeline), icon: '📋' },
    { id: 'discussion_v1', name: t(K.workflow.tabDiscussion), icon: '💬' }
  ]

  const handleStart = (payload) => {
    onStart(payload)
  }

  return (
    <div className="fixed inset-0 bg-black/45 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={onClose}>
      <div
        className="bg-gray-900/80 backdrop-blur-md border border-gray-700/50 rounded-2xl p-6 w-[28rem] shadow-[0_20px_50px_rgba(0,0,0,0.55)] max-h-[85vh] overflow-y-auto animate-scale-up flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-white font-bold text-base">⚡ {t(K.workflow.titleLabel) || '启动工作流'}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-sm font-semibold transition-colors">✕</button>
        </div>

        {/* Tab Selection */}
        <div className="flex border-b border-gray-800 mb-4">
          {tabs.map(tab => {
            const isActive = activeTab === tab.id
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex-1 py-2 text-xs font-bold transition-all flex items-center justify-center gap-1.5 border-b-2 ${
                  isActive
                    ? 'border-indigo-500 text-white bg-indigo-500/5'
                    : 'border-transparent text-gray-400 hover:text-gray-250 hover:bg-gray-800/10'
                }`}
              >
                <span>{tab.icon}</span>
                <span>{tab.name}</span>
              </button>
            )
          })}
        </div>

        {/* Config Panel Slots */}
        <div className="flex-1">
          {activeTab === 'workflow_v1' ? (
            <RequirementPanel groupBots={groupBots} onStart={handleStart} onClose={onClose} />
          ) : (
            <DiscussionPanel groupBots={groupBots} onStart={handleStart} onClose={onClose} />
          )}
        </div>
      </div>
    </div>
  )
}
