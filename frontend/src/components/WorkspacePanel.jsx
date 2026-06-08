import { useState, useEffect, useCallback } from 'react'
import SkillPanel from './SkillPanel'

export default function WorkspacePanel({ bot, groupId, onClose }) {
  const [showSkills, setShowSkills] = useState(false)
  const [tree, setTree] = useState([])
  const [selected, setSelected] = useState(null)
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [historyVersions, setHistoryVersions] = useState([])
  const [historySelectedTs, setHistorySelectedTs] = useState(null)
  const [historyPreviewContent, setHistoryPreviewContent] = useState(null)
  const [historyLoading, setHistoryLoading] = useState(false)

  const loadTree = useCallback(async () => {
    const res = await fetch(`/api/members/${bot.id}/workspace`)
    if (res.ok) setTree(await res.json())
  }, [bot.id])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const res = await fetch(`/api/members/${bot.id}/workspace`)
      if (!cancelled && res.ok) setTree(await res.json())
    })()
    return () => { cancelled = true }
  }, [bot.id])

  // DFT-062: this early return must come AFTER every hook above. It used to sit
  // before the other ~13 hooks, so toggling the skills panel changed how many
  // hooks ran between renders and crashed React (Rules of Hooks violation).
  if (showSkills) {
    return <SkillPanel bot={bot} groupId={groupId} onClose={() => setShowSkills(false)} />
  }

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

  const openHistory = async () => {
    if (!selected) return
    setHistoryLoading(true)
    setShowHistory(true)
    setHistorySelectedTs(null)
    setHistoryPreviewContent(null)
    const res = await fetch(`/api/members/${bot.id}/workspace/history?path=${encodeURIComponent(selected)}`)
    if (res.ok) {
      const data = await res.json()
      setHistoryVersions(data.versions)
    }
    setHistoryLoading(false)
  }

  const loadHistoryVersion = async (ts) => {
    setHistorySelectedTs(ts)
    const res = await fetch(
      `/api/members/${bot.id}/workspace/history/version?path=${encodeURIComponent(selected)}&ts=${ts}`
    )
    if (res.ok) {
      const data = await res.json()
      setHistoryPreviewContent(data.content)
    }
  }

  const restoreVersion = () => {
    if (historyPreviewContent === null) return
    setContent(historyPreviewContent)
    setDirty(true)
    setShowHistory(false)
  }

  const formatTs = (ts) => {
    const m = ts.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/)
    if (!m) return ts
    return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`
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

  const newFile = async () => {
    const input = prompt('新建文件路径（相对工作区，例：skills/mytool.md 或 skills/mytool/run.sh）：', 'skills/')
    const path = (input || '').trim()
    if (!path || path.endsWith('/')) return
    // .md skills get a frontmatter starter so discovery can parse them
    const starter = path.endsWith('.md') ? '---\nname: \ndescription: \n---\n\n' : ''
    const res = await fetch(`/api/members/${bot.id}/workspace/file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content: starter }),
    })
    if (!res.ok) { alert('创建失败：' + (await res.text())); return }
    await loadTree()
    openFile(path)
  }

  const newDir = async () => {
    const input = prompt('新建文件夹路径（相对工作区，例：skills/mytool）：', 'skills/')
    const path = (input || '').trim().replace(/\/+$/, '')
    if (!path) return
    const res = await fetch(`/api/members/${bot.id}/workspace/dir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!res.ok) { alert('创建失败：' + (await res.text())); return }
    await loadTree()
  }

  const deletePath = async (path, isDir) => {
    if (!confirm(`确认删除${isDir ? '文件夹' : '文件'}「${path}」${isDir ? '及其全部内容' : ''}？`)) return
    const res = await fetch(
      `/api/members/${bot.id}/workspace/file?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' }
    )
    if (!res.ok) { alert('删除失败：' + (await res.text())); return }
    if (selected === path || (isDir && selected && selected.startsWith(path + '/'))) {
      setSelected(null); setContent(''); setDirty(false)
    }
    await loadTree()
  }

  const root = buildTree(tree)

  return (
    <div className="fixed inset-0 bg-black/45 backdrop-blur-sm flex items-center justify-center z-50 animate-fade-in" onClick={onClose}>
      <div
        className="bg-gray-900/80 backdrop-blur-md border border-gray-700/50 rounded-2xl shadow-[0_20px_50px_rgba(0,0,0,0.55)] flex overflow-hidden animate-scale-up"
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
            <div className="mt-2 flex gap-1.5">
              <button
                onClick={newFile}
                className="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 text-gray-200 transition-colors"
              >
                + 文件
              </button>
              <button
                onClick={newDir}
                className="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 text-gray-200 transition-colors"
              >
                + 文件夹
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto py-2">
            {tree.length === 0 && (
              <div className="px-3 text-xs text-gray-500 mt-2">（空）</div>
            )}
            <TreeLevel node={root} depth={0} prefix="" selected={selected} onOpen={openFile} onDelete={deletePath} />
          </div>
        </div>

        {/* Right: editor or history viewer */}
        <div className="flex-1 flex flex-col min-w-0">
          {showHistory ? (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
                <button onClick={() => setShowHistory(false)} className="text-xs text-gray-400 hover:text-white flex items-center gap-1">
                  ← 返回编辑
                </button>
                <span className="text-xs text-gray-400 font-mono">{selected} · 历史版本</span>
                <button
                  onClick={restoreVersion}
                  disabled={historyPreviewContent === null}
                  className="text-xs px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white transition-colors"
                >
                  恢复此版本
                </button>
              </div>
              <div className="flex flex-1 overflow-hidden">
                {/* Version list */}
                <div className="w-44 flex-shrink-0 border-r border-gray-700 overflow-y-auto py-1">
                  {historyLoading ? (
                    <div className="text-xs text-gray-500 p-3">加载中…</div>
                  ) : historyVersions.length === 0 ? (
                    <div className="text-xs text-gray-600 p-3">暂无历史版本</div>
                  ) : historyVersions.map(v => (
                    <button
                      key={v.ts}
                      onClick={() => loadHistoryVersion(v.ts)}
                      className={`w-full text-left px-3 py-2 text-xs transition-colors ${
                        historySelectedTs === v.ts
                          ? 'bg-indigo-600/30 text-indigo-300'
                          : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                      }`}
                    >
                      <div>{formatTs(v.ts)}</div>
                      <div className="text-gray-600 mt-0.5">{(v.size / 1024).toFixed(1)} KB</div>
                    </button>
                  ))}
                </div>
                {/* Version content preview */}
                <div className="flex-1 overflow-hidden">
                  {historyPreviewContent !== null ? (
                    <textarea
                      className="w-full h-full bg-gray-900 text-gray-500 text-sm font-mono p-4 resize-none outline-none"
                      value={historyPreviewContent}
                      readOnly
                    />
                  ) : (
                    <div className="flex items-center justify-center h-full text-gray-600 text-sm">
                      选择版本查看内容
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
                <span className="text-sm text-gray-300 font-mono">{selected || '选择文件查看'}</span>
                <div className="flex items-center gap-2">
                  {dirty && <span className="text-xs text-yellow-400">未保存</span>}
                  {selected && (
                    <button
                      onClick={openHistory}
                      className="text-xs px-2 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
                    >
                      历史
                    </button>
                  )}
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
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// Build a nested {name, dirs, files} tree from the flat [{path, name, is_dir}] list.
function buildTree(flat) {
  const root = { dirs: {}, files: [] }
  const ensureDir = (parts) => {
    let node = root
    for (const part of parts) {
      if (!node.dirs[part]) node.dirs[part] = { name: part, dirs: {}, files: [] }
      node = node.dirs[part]
    }
    return node
  }
  flat.forEach(n => {
    const parts = n.path.split('/')
    if (n.is_dir) {
      ensureDir(parts)
    } else {
      const node = parts.length > 1 ? ensureDir(parts.slice(0, -1)) : root
      node.files.push(n)
    }
  })
  return root
}

// Recursively render one tree level: files first, then nested directories.
function TreeLevel({ node, depth, prefix, selected, onOpen, onDelete }) {
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  const dirNames = Object.keys(node.dirs).sort()
  return (
    <>
      {files.map(f => (
        <FileRow
          key={f.path} name={f.name} depth={depth} active={selected === f.path}
          onClick={() => onOpen(f.path)} onDelete={() => onDelete(f.path, false)}
        />
      ))}
      {dirNames.map(name => {
        const dirPath = prefix ? `${prefix}/${name}` : name
        return (
          <div key={name}>
            <div
              className="group/row py-1 text-xs text-gray-500 font-medium mt-1 flex items-center gap-1 hover:bg-gray-800/60"
              style={{ paddingLeft: `${12 + depth * 14}px`, paddingRight: '6px' }}
            >
              <span>📁</span>
              <span className="truncate flex-1">{name}</span>
              <button
                onClick={() => onDelete(dirPath, true)}
                title="删除文件夹"
                className="opacity-0 group-hover/row:opacity-100 text-gray-500 hover:text-red-400 px-1 flex-shrink-0 transition-opacity"
              >
                🗑
              </button>
            </div>
            <TreeLevel
              node={node.dirs[name]} depth={depth + 1} prefix={dirPath}
              selected={selected} onOpen={onOpen} onDelete={onDelete}
            />
          </div>
        )
      })}
    </>
  )
}

function FileRow({ name, active, depth = 0, onClick, onDelete }) {
  return (
    <div
      className={`group/row w-full text-xs flex items-center transition-colors ${
        active ? 'bg-indigo-600/30 text-indigo-300' : 'text-gray-400 hover:bg-gray-800 hover:text-white'
      }`}
      style={{ paddingRight: '6px' }}
    >
      <button
        onClick={onClick}
        className="flex-1 min-w-0 text-left py-1.5 flex items-center gap-1.5"
        style={{ paddingLeft: `${12 + depth * 14}px` }}
      >
        <span className="text-gray-500">📄</span>
        <span className="truncate">{name}</span>
      </button>
      <button
        onClick={onDelete}
        title="删除文件"
        className="opacity-0 group-hover/row:opacity-100 text-gray-500 hover:text-red-400 px-1 flex-shrink-0 transition-opacity"
      >
        🗑
      </button>
    </div>
  )
}
