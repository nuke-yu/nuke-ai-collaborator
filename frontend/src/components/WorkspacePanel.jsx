import { useState, useEffect, useCallback } from 'react'
import SkillPanel from './SkillPanel'

export default function WorkspacePanel({ bot, groupId, onClose }) {
  const [showSkills, setShowSkills] = useState(false)
  if (showSkills) {
    return <SkillPanel bot={bot} groupId={groupId} onClose={() => setShowSkills(false)} />
  }

  const [tree, setTree] = useState([])
  const [selected, setSelected] = useState(null)
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const loadTree = useCallback(async () => {
    const res = await fetch(`/api/members/${bot.id}/workspace`)
    if (res.ok) setTree(await res.json())
  }, [bot.id])

  useEffect(() => { loadTree() }, [loadTree])

  const openFile = async (path) => {
    if (dirty && !confirm('有未保存的修改，确认切换？')) return
    const res = await fetch(`/api/members/${bot.id}/workspace/file?path=${encodeURIComponent(path)}`)
    if (res.ok) {
      const data = await res.json()
      setSelected(path)
      setContent(data.content)
      setDirty(false)
    }
  }

  const saveFile = async () => {
    if (!selected) return
    setSaving(true)
    await fetch(`/api/members/${bot.id}/workspace/file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: selected, content }),
    })
    setSaving(false)
    setDirty(false)
  }

  const files = tree.filter(n => !n.is_dir)
  const dirs = tree.filter(n => n.is_dir)

  const filesByDir = {}
  files.forEach(f => {
    const parts = f.path.split('/')
    const dir = parts.length > 1 ? parts[0] : ''
    if (!filesByDir[dir]) filesByDir[dir] = []
    filesByDir[dir].push(f)
  })

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-800 rounded-2xl shadow-xl flex overflow-hidden"
        style={{ width: '860px', maxWidth: '95vw', height: '560px', maxHeight: '90vh' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Left: file tree */}
        <div className="w-52 flex-shrink-0 bg-gray-900 flex flex-col border-r border-gray-700">
          <div className="px-3 py-3 border-b border-gray-700">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-0.5">工作区</div>
            <div className="text-sm text-white font-medium truncate">{bot.name}</div>
            <button
              onClick={() => setShowSkills(true)}
              className="mt-2 w-full text-xs px-2 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 text-left transition-colors flex items-center gap-1.5"
            >
              <span>⚡</span> Skill 管理
            </button>
          </div>
          <div className="flex-1 overflow-y-auto py-2">
            {tree.length === 0 && (
              <div className="px-3 text-xs text-gray-500 mt-2">（空）</div>
            )}
            {/* Root files */}
            {(filesByDir[''] || []).map(f => (
              <FileRow
                key={f.path} name={f.name} active={selected === f.path}
                onClick={() => openFile(f.path)}
              />
            ))}
            {/* Subdirectories */}
            {dirs.map(d => (
              <div key={d.path}>
                <div className="px-3 py-1 text-xs text-gray-500 font-medium mt-1 flex items-center gap-1">
                  <span>📁</span>{d.name}
                </div>
                {(filesByDir[d.name] || []).map(f => (
                  <FileRow
                    key={f.path} name={f.name} indent active={selected === f.path}
                    onClick={() => openFile(f.path)}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* Right: editor */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
            <span className="text-sm text-gray-300 font-mono">{selected || '选择文件查看'}</span>
            <div className="flex items-center gap-2">
              {dirty && <span className="text-xs text-yellow-400">未保存</span>}
              {selected && (
                <button
                  onClick={saveFile}
                  disabled={saving || !dirty}
                  className="text-xs px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors"
                >
                  {saving ? '保存中…' : '保存'}
                </button>
              )}
              <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none">×</button>
            </div>
          </div>
          <div className="flex-1 overflow-hidden">
            {selected ? (
              <textarea
                className="w-full h-full bg-gray-900 text-gray-100 text-sm font-mono p-4 resize-none outline-none"
                value={content}
                onChange={e => { setContent(e.target.value); setDirty(true) }}
                onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 's') { e.preventDefault(); saveFile() } }}
              />
            ) : (
              <div className="flex items-center justify-center h-full text-gray-600 text-sm">
                从左侧选择文件
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function FileRow({ name, active, indent, onClick }) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-1.5 transition-colors ${
        active ? 'bg-indigo-600/30 text-indigo-300' : 'text-gray-400 hover:bg-gray-800 hover:text-white'
      } ${indent ? 'pl-7' : ''}`}
    >
      <span className="text-gray-500">📄</span>
      <span className="truncate">{name}</span>
    </button>
  )
}
