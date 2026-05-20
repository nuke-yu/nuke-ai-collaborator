export default function WorkflowStartModal({ stages, onChangeStages, onStart, onClose }) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 border border-gray-700 rounded-2xl p-6 w-96 shadow-2xl max-h-[80vh] overflow-y-auto"
        onClick={e => e.stopPropagation()}
      >
        <h3 className="text-white font-semibold mb-1">启动工作流</h3>
        <p className="text-gray-400 text-xs mb-4">
          Bot 将按顺序依次接管对话；多人并行阶段会自动抢单分配任务。
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

        <p className="text-gray-600 text-xs mb-4">Bot 说出对应关键词时自动进入下一阶段</p>

        <div className="flex gap-2">
          <button
            onClick={onStart}
            className="flex-1 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg py-2 text-sm font-medium transition-colors"
          >
            ⚡ 启动
          </button>
          <button
            onClick={onClose}
            className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded-lg py-2 text-sm transition-colors"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  )
}

function SingleStageRow({ index, stage, onChange }) {
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
        placeholder="完成关键词"
        value={stage.done_keyword || ''}
        onChange={e => onChange(e.target.value)}
      />
    </div>
  )
}

function PoolStageRow({ index, stage, onChange }) {
  return (
    <div className="border border-indigo-800/50 rounded-lg p-3 bg-indigo-950/30">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-gray-600 text-xs w-4 flex-shrink-0">{index + 1}.</span>
        <span className="text-xs text-indigo-400 font-medium">并行开发阶段</span>
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
        <span className="text-gray-600 text-xs flex-shrink-0">完成词</span>
        <input
          className="flex-1 bg-gray-700 text-white text-xs rounded px-2 py-1 outline-none focus:ring-1 focus:ring-indigo-500 placeholder-gray-600"
          placeholder="所有人说完此词后进入下一阶段"
          value={stage.done_keyword || ''}
          onChange={e => onChange(e.target.value)}
        />
      </div>
      <p className="text-gray-600 text-xs mt-1.5">
        上一阶段会生成任务列表，开发者依次认领，做完一个自动领取下一个
      </p>
    </div>
  )
}
