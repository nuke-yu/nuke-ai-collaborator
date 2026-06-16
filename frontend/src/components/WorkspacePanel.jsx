import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'
import SkillPanel from './SkillPanel'

export default function WorkspacePanel({ bot, groupId, onClose }) {
  const { t } = useTranslation()
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
    if (res.ok) {
      const botTree = await res.json()
      // Add shared workspace (workspace/<project>/*)
      const sharedRes = await fetch(`/api/groups/${groupId}/workspace`)
      if (sharedRes.ok) {
        const sharedTree = await sharedRes.json()
        setTree(mergeUniqueByPath(botTree, sharedTree))
      } else {
        setTree(botTree)
      }
    }
  }, [bot.id, groupId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const res = await fetch(`/api/members/${bot.id}/workspace`)
      if (!cancelled && res.ok) {
        const botTree = await res.json()
        const sharedRes = await fetch(`/api/groups/${groupId}/workspace`)
        if (sharedRes.ok) {
          const sharedTree = await sharedRes.json()
          if (!cancelled) setTree(mergeUniqueByPath(botTree, sharedTree))
        } else {
          if (!cancelled) setTree(botTree)
        }
      }
    })()
    return () => { cancelled = true }
  }, [bot.id, groupId])

  // DFT-062: this early return must come AFTER every hook above. It used to sit
  // before the other ~13 hooks, so toggling the skills panel changed how many
  // hooks ran between renders and crashed React (Rules of Hooks violation).
  if (showSkills) {
    return <SkillPanel bot={bot} groupId={groupId} onClose={() => setShowSkills(false)} />
  }

  // Filter out shared workspace files from editing (read-only)
  const isSharedFile = (path) => path.startsWith("workspace/") || path.startsWith("docs/") || path.startsWith("prs/")

  const openFile = async (path) => {
    if (dirty && !confirm(t(K.workspace.unsavedConfirm))) return
    if (isSharedFile(path)) {
      alert(t(K.workspace.sharedReadonlyAlert))
      return
    }
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
    const input = prompt(t(K.workspace.newFilePrompt), t(K.workspace.newFileDefault))
    const path = (input || '').trim()
    if (!path || path.endsWith('/')) return
    // .md skills get a frontmatter starter so discovery can parse them
    const starter = path.endsWith('.md') ? '---\nname: \ndescription: \n---\n\n' : ''
    const res = await fetch(`/api/members/${bot.id}/workspace/file`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content: starter }),
    })
    if (!res.ok) { alert(t(K.workspace.createFailed) + (await res.text())); return }
    await loadTree()
    openFile(path)
  }

  const newDir = async () => {
    const input = prompt(t(K.workspace.newFolderPrompt), t(K.workspace.newFolderDefault))
    const path = (input || '').trim().replace(/\/+$/, '')
    if (!path) return
    const res = await fetch(`/api/members/${bot.id}/workspace/dir`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    })
    if (!res.ok) { alert(t(K.workspace.createFailed) + (await res.text())); return }
    await loadTree()
  }

  const deletePath = async (path, isDir) => {
    const confirmMsg = isDir
      ? t(K.workspace.confirmDeleteDir, { path })
      : t(K.workspace.confirmDeleteFile, { path })
    if (!confirm(confirmMsg)) return
    const res = await fetch(
      `/api/members/${bot.id}/workspace/file?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' }
    )
    if (!res.ok) { alert(t(K.workspace.deleteFailed) + (await res.text())); return }
    if (selected === path || (isDir && selected && selected.startsWith(path + '/'))) {
      setSelected(null); setContent(''); setDirty(false)
    }
    await loadTree()
  }

  const root = buildTree(tree, t(K.workspace.sharedZone))

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
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-0.5">{t(K.workspace.title)}</div>
            <div className="text-sm text-white font-medium truncate">{bot.name}</div>
            <button
              onClick={() => setShowSkills(true)}
              className="mt-2 w-full text-xs px-2 py-1.5 rounded-lg bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 text-left transition-colors flex items-center gap-1.5"
            >
              <span>⚡</span> {t(K.workspace.skillManage)}
            </button>
            <div className="mt-2 flex gap-1.5">
              <button
                onClick={newFile}
                className="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 text-gray-200 transition-colors"
              >
                {t(K.workspace.addFile)}
              </button>
              <button
                onClick={newDir}
                className="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-700/60 hover:bg-gray-600 text-gray-200 transition-colors"
              >
                {t(K.workspace.addFolder)}
              </button>
            </div>
            <div className="mt-2 text-[10px] text-gray-500 flex items-center gap-1">
              <span title={t(K.workspace.sharedZoneReadonly)}>🌐</span>
              <span>{t(K.workspace.sharedZone)}</span>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto py-2">
            {tree.length === 0 && (
              <div className="px-3 text-xs text-gray-500 mt-2">{t(K.workspace.empty)}</div>
            )}
            <TreeLevel node={root} depth={0} prefix="" selected={selected} onOpen={openFile} onDelete={deletePath} isShared={false} t={t} />
          </div>
        </div>

        {/* Right: editor or history viewer */}
        <div className="flex-1 flex flex-col min-w-0">
          {showHistory ? (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
                <button onClick={() => setShowHistory(false)} className="text-xs text-gray-400 hover:text-white flex items-center gap-1">
                  {t(K.workspace.backToEditor)}
                </button>
                <span className="text-xs text-gray-400 font-mono">{selected} · {t(K.workspace.historyVersions)}</span>
                <button
                  onClick={restoreVersion}
                  disabled={historyPreviewContent === null}
                  className="text-xs px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white transition-colors"
                >
                  {t(K.workspace.restoreVersion)}
                </button>
              </div>
              <div className="flex flex-1 overflow-hidden">
                {/* Version list */}
                <div className="w-44 flex-shrink-0 border-r border-gray-700 overflow-y-auto py-1">
                  {historyLoading ? (
                    <div className="text-xs text-gray-500 p-3">{t(K.workspace.loading)}</div>
                  ) : historyVersions.length === 0 ? (
                    <div className="text-xs text-gray-600 p-3">{t(K.workspace.noHistory)}</div>
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
                      {t(K.workspace.selectVersion)}
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700 flex-shrink-0">
                <span className="text-sm text-gray-300 font-mono">{selected || t(K.workspace.selectFile)}</span>
                <div className="flex items-center gap-2">
                  {dirty && <span className="text-xs text-yellow-400">{t(K.workspace.unsaved)}</span>}
                  {selected && (
                    <button
                      onClick={openHistory}
                      className="text-xs px-2 py-1 rounded-lg bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
                    >
                      {t(K.workspace.history)}
                    </button>
                  )}
                  {selected && (
                    <button
                      onClick={saveFile}
                      disabled={saving || !dirty}
                      className="text-xs px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white transition-colors"
                    >
                      {saving ? t(K.workspace.saving) : t(K.workspace.save)}
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
                    {t(K.workspace.selectFilePrompt)}
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

// Merge node lists, keeping the first occurrence of each path. The bot workspace
// endpoint already includes shared workspace/ files, and the group endpoint returns
// them again — without this dedup the flat list carries duplicate paths, which makes
// buildTree emit two FileRows with the same React key.
function mergeUniqueByPath(...lists) {
  const seen = new Set()
  const out = []
  for (const list of lists) {
    for (const n of list) {
      if (seen.has(n.path)) continue
      seen.add(n.path)
      out.push(n)
    }
  }
  return out
}

// Build a nested tree, splitting shared workspace into its own root section
function buildTree(flat, sharedZoneLabel) {
  const root = { dirs: {}, files: [] }
  const sharedSection = { dirs: {}, files: [] }
  const SHARED_PREFIXES = ['workspace/', 'docs/', 'prs/']

  const ensureDir = (parts, section) => {
    let node = section
    for (const part of parts) {
      if (!node.dirs[part]) node.dirs[part] = { name: part, dirs: {}, files: [] }
      node = node.dirs[part]
    }
    return node
  }

  flat.forEach(n => {
    const parts = n.path.split('/')
    const isShared = SHARED_PREFIXES.some(p => n.path.startsWith(p))
    const section = isShared ? sharedSection : root

    if (n.is_dir) {
      ensureDir(parts, section)
    } else {
      const node = parts.length > 1 ? ensureDir(parts.slice(0, -1), section) : section
      node.files.push(n)
    }
  })

  // Merge shared section into root if it has content
  if (Object.keys(sharedSection.dirs).length > 0 || sharedSection.files.length > 0) {
    root.dirs[`🌐 ${sharedZoneLabel}`] = sharedSection
  }

  return root
}

// Recursively render one tree level: files first, then nested directories.
function TreeLevel({ node, depth, prefix, selected, onOpen, onDelete, isShared = false, t }) {
  const files = [...node.files].sort((a, b) => a.name.localeCompare(b.name))
  const dirNames = Object.keys(node.dirs).sort()
  return (
    <>
      {files.map(f => (
        <FileRow
          key={f.path} name={f.name} depth={depth} active={selected === f.path} isShared={isShared}
          onClick={() => onOpen(f.path)} onDelete={() => onDelete(f.path, false)}
          sharedZoneReadonly={t('workspace.sharedZoneReadonly')}
          deleteFileTitle={t('workspace.deleteFileTitle')}
        />
      ))}
      {dirNames.map(name => {
        const dirPath = prefix ? `${prefix}/${name}` : name
        // Shared workspace is the root (starts with workspace/, docs/, prs/)
        const childIsShared = !prefix && isShared
        return (
          <div key={name}>
            <div
              className="group/row py-1 text-xs text-gray-500 font-medium mt-1 flex items-center gap-1 hover:bg-gray-800/60"
              style={{ paddingLeft: `${12 + depth * 14}px`, paddingRight: '6px' }}
            >
              <span>📁</span>
              <span className="truncate flex-1">{isShared && <span title={t('workspace.sharedZoneReadonly')}>🌐</span>} {name}</span>
              {!isShared && (
                <button
                  onClick={() => onDelete(dirPath, true)}
                  title={t('workspace.deleteDirTitle')}
                  className="opacity-0 group-hover/row:opacity-100 text-gray-500 hover:text-red-400 px-1 flex-shrink-0 transition-opacity"
                >
                  🗑
                </button>
              )}
            </div>
            <TreeLevel
              node={node.dirs[name]} depth={depth + 1} prefix={dirPath}
              selected={selected} onOpen={onOpen} onDelete={onDelete} isShared={childIsShared} t={t}
            />
          </div>
        )
      })}
    </>
  )
}

function FileRow({ name, active, depth = 0, onClick, onDelete, isShared = false, sharedZoneReadonly, deleteFileTitle }) {
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
        <span className="truncate">{isShared && <span title={sharedZoneReadonly}>🌐</span>} {name}</span>
      </button>
      {!isShared && (
        <button
          onClick={onDelete}
          title={deleteFileTitle}
          className="opacity-0 group-hover/row:opacity-100 text-gray-500 hover:text-red-400 px-1 flex-shrink-0 transition-opacity"
        >
          🗑
        </button>
      )}
    </div>
  )
}
