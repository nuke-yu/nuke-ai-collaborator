import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

export default function WorkflowStartModal({ stages, onChangeStages, onStart, onClose }) {
  const { t } = useTranslation()

  return (
    <div className="fixed inset-0 bg-black/45 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={onClose}>
      <div
        className="bg-gray-900/80 backdrop-blur-md border border-gray-700/50 rounded-2xl p-6 w-96 shadow-[0_20px_50px_rgba(0,0,0,0.55)] max-h-[80vh] overflow-y-auto animate-scale-up"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-white font-semibold mb-1">{t(K.workflow.startTitle)}</h3>
        <p className="text-gray-400 text-xs mb-4">
          {t(K.workflow.startDescription)}
        </p>

        <div className="space-y-2 mb-5">
          {stages.map((stage, i) =>
            stage.stage_type === 'pool' ? (
              <PoolStageRow
                key={i}
                index={i}
                stage={stage}
                onChange={kw => onChangeStages(prev => prev.map((s, j) => j === i ? { ...s, done_keyword: kw } : s))}
              />
            ) : (
              <SingleStageRow
                key={i}
                index={i}
                stage={stage}
                onChange={kw => onChangeStages(prev => prev.map((s, j) => j === i ? { ...s, done_keyword: kw } : s))}
              />
            )
          )}
        </div>

        <p className="text-gray-600 text-xs mb-4">{t(K.workflow.doneKeywordHint)}</p>

        <div className="flex gap-2">
          <button
            onClick={onStart}
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
    </div>
  )
}

function SingleStageRow({ index, stage, onChange }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2">
      <span className="text-gray-600 text-xs w-4 flex-shrink-0">{index + 1}.</span>
      <div
        className="w-5 h-5 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold text-white"
        style={{ backgroundColor: stage.avatar_color }}
      >
        {stage.name[0]}
      </div>
      <span className="text-gray-300 text-sm w-20 flex-shrink-0 truncate">{stage.name}</span>
      <input
        className="flex-1 bg-gray-700 text-white text-xs rounded px-2 py-1 outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-600"
        placeholder={t(K.workflow.doneKeywordLabel)}
        value={stage.done_keyword || ''}
        onChange={e => onChange(e.target.value)}
      />
    </div>
  )
}

function PoolStageRow({ index, stage, onChange }) {
  const { t } = useTranslation()
  return (
    <div className="border border-indigo-800/50 rounded-lg p-3 bg-indigo-950/30">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-gray-600 text-xs w-4 flex-shrink-0">{index + 1}.</span>
        <span className="text-xs text-indigo-400 font-medium">{t(K.workflow.parallelStage)}</span>
        <div className="flex gap-1 flex-1">
          {stage.bots.map(b => (
            <div
              key={b.id}
              className="w-5 h-5 rounded-full flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
              style={{ backgroundColor: b.avatar_color }}
              title={b.name}
            >
              {b.name[0]}
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-gray-600 text-xs flex-shrink-0">{t(K.workflow.doneWordLabel)}</span>
        <input
          className="flex-1 bg-gray-700 text-white text-xs rounded px-2 py-1 outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-600"
          placeholder={t(K.workflow.allDoneHint)}
          value={stage.done_keyword || ''}
          onChange={e => onChange(e.target.value)}
        />
      </div>
      <p className="text-gray-600 text-xs mt-1.5">
        {t(K.workflow.poolHint)}
      </p>
    </div>
  )
}
