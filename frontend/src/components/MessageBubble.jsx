import { useState, useRef, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import * as wsrpc from '../wsrpc'
import EmojiPicker from './EmojiPicker'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'

function CollapsibleCode({ language, code }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const lines = code.split('\n').length

  const handleCopy = (e) => {
    e.stopPropagation()
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="my-2 rounded-lg overflow-hidden border border-gray-700">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-800 hover:bg-gray-750 text-xs text-gray-400 transition-colors"
      >
        <span className="flex items-center gap-2">
          <span className="text-indigo-400 font-mono">{language || 'code'}</span>
          <span>{lines} 行</span>
        </span>
        <span className="flex items-center gap-2">
          <span
            role="button"
            onClick={handleCopy}
            className={`transition-colors ${copied ? 'text-green-400' : 'text-gray-500 hover:text-gray-300'}`}
          >
            {copied ? '✓ 已复制' : '复制'}
          </span>
          <span className="text-gray-500">{open ? '▲ 收起' : '▼ 展开'}</span>
        </span>
      </button>
      {open && (
        <SyntaxHighlighter
          style={oneDark}
          language={language}
          PreTag="div"
          className="!m-0 !rounded-none text-xs"
        >
          {code}
        </SyntaxHighlighter>
      )}
    </div>
  )
}

function Lightbox({ src, onClose }) {
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center cursor-zoom-out"
      onClick={onClose}
    >
      <img
        src={src}
        alt=""
        className="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-white/70 hover:text-white text-2xl leading-none"
      >
        ✕
      </button>
    </div>
  )
}

function MentionText({ children }) {
  if (typeof children !== 'string') return children
  const parts = children.split(/(@\S+)/g)
  return parts.map((part, i) =>
    /^@\S+/.test(part)
      ? <span key={i} className="text-indigo-400 font-semibold bg-indigo-950/50 rounded px-0.5">{part}</span>
      : part
  )
}

const mdComponents = {
  code({ node, inline, className, children, ...props }) {
    const match = /language-(\w+)/.exec(className || '')
    const language = match?.[1] || ''
    const code = String(children).replace(/\n$/, '')
    
    // Point 5: Terminal Aesthetic for Shell Commands
    if (!inline && (language === 'bash' || language === 'shell' || code.startsWith('exit_code:'))) {
      return (
        <div className="my-2 rounded-lg overflow-hidden border border-gray-700 shadow-xl">
          <div className="bg-gray-800 px-3 py-1.5 flex items-center gap-1.5 border-b border-gray-700">
            <div className="flex gap-1">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
            </div>
            <span className="text-[10px] text-gray-500 font-mono uppercase tracking-wider ml-1">Terminal — {language || 'output'}</span>
          </div>
          <div className="bg-black p-4 font-mono text-[13px] leading-relaxed overflow-x-auto selection:bg-indigo-500/30">
            <div className="text-green-400/90 mb-1 flex gap-2">
              <span className="shrink-0 text-indigo-500/70 select-none">🤖 $</span>
              <span className="whitespace-pre-wrap text-indigo-100/90">{code}</span>
            </div>
          </div>
        </div>
      )
    }

    if (!inline && (match || code.includes('\n'))) {
      return <CollapsibleCode language={language} code={code} />
    }
    return (
      <code className="bg-gray-700 text-pink-300 rounded px-1 py-0.5 text-xs font-mono" {...props}>
        {children}
      </code>
    )
  },
  p({ children }) {
    return <p className="mb-1 last:mb-0"><MentionText>{children}</MentionText></p>
  },
  li({ children }) {
    return <li><MentionText>{children}</MentionText></li>
  },
  a({ href, children }) {
    return <a href={href} target="_blank" rel="noreferrer" className="text-indigo-400 underline hover:text-indigo-300">{children}</a>
  },
  blockquote({ children }) {
    return <blockquote className="border-l-2 border-gray-500 pl-3 text-gray-400 my-1">{children}</blockquote>
  },
  ul({ children }) {
    return <ul className="list-disc list-inside space-y-0.5 my-1">{children}</ul>
  },
  ol({ children }) {
    return <ol className="list-decimal list-inside space-y-0.5 my-1">{children}</ol>
  },
  h1({ children }) { return <h1 className="text-base font-bold mt-2 mb-1">{children}</h1> },
  h2({ children }) { return <h2 className="text-sm font-bold mt-2 mb-1">{children}</h2> },
  h3({ children }) { return <h3 className="text-sm font-semibold mt-1 mb-0.5">{children}</h3> },
  table({ children }) {
    return <div className="overflow-x-auto my-2"><table className="text-xs border-collapse">{children}</table></div>
  },
  th({ children }) {
    return <th className="border border-gray-600 px-2 py-1 bg-gray-700 font-semibold">{children}</th>
  },
  td({ children }) {
    return <td className="border border-gray-600 px-2 py-1">{children}</td>
  },
}

function SkillsStrip({ skills }) {
  const [open, setOpen] = useState(false)
  const injected = skills.filter(s => s.injected)
  if (!injected.length) return null
  return (
    <div className="mb-1.5 text-xs text-gray-500">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 hover:text-gray-300 transition-colors"
      >
        <span>⚡</span>
        <span>{injected.length} 个技能已加载</span>
        <span className="text-gray-600 ml-0.5">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="flex flex-wrap gap-1 mt-1">
          {injected.map(s => (
            <span
              key={s.name}
              title={s.injected === 'full' ? '常驻技能' : '按需注入'}
              className={`px-1.5 py-0.5 rounded text-xs ${
                s.injected === 'full'
                  ? 'bg-green-900/40 text-green-400 border border-green-800/50'
                  : 'bg-indigo-900/40 text-indigo-400 border border-indigo-800/50'
              }`}
            >
              {s.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

const QUICK_EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🎉', '🤔', '👏']

// 工作流哨兵标记（如 [[BA_DONE]] / [[DEV_DONE]] / [[QA_DONE]]）是给编排器看的控制信号，
// 不展示给人——后端靠它挂起确认门，前端渲染时把它（含全角括号变体）抹掉。
const stripSentinels = (text) =>
  typeof text === 'string'
    ? text.replace(/[\[【]{1,2}\s*[A-Za-z_]*_DONE\s*[\]】]{1,2}/g, '').replace(/\n{3,}/g, '\n\n').trim()
    : text

export default function MessageBubble({ msg, isTyping, currentMemberId, members = [], readMap = {}, onReply, reactions = {}, onReact, isPinned, onPin, onUnpin, highlighted = false, onConfirmGate }) {
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState(msg.content)
  const [lightboxSrc, setLightboxSrc] = useState(null)
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)
  const [gateState, setGateState] = useState(null)  // null | 'confirmed' | 'revising'
  const editRef = useRef(null)
  const isOwn = msg.member_id === currentMemberId
  const closeEmojiPicker = useCallback(() => setShowEmojiPicker(false), [])

  useEffect(() => { if (editing) editRef.current?.focus() }, [editing])

  const handleEditSave = async () => {
    const trimmed = editText.trim()
    if (!trimmed || trimmed === msg.content) { setEditing(false); return }
    // author check is enforced server-side from the authenticated WS connection
    wsrpc.send({ type: 'mutate', action: 'edit', msg_id: msg.id, content: trimmed })
    setEditing(false)
  }

  const handleWithdraw = async () => {
    wsrpc.send({ type: 'mutate', action: 'withdraw', msg_id: msg.id })
  }
  const avatar = (
    <div className="relative flex-shrink-0 group/avatar">
      <div
        className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold text-white shadow-inner transition-all duration-500 ${
          msg.sender_type === 'bot' 
            ? 'ring-2 ring-indigo-500/30 group-hover/avatar:ring-indigo-400 group-hover/avatar:shadow-[0_0_12px_rgba(99,102,241,0.4)]' 
            : 'ring-1 ring-gray-700/50'
        }`}
        style={{ backgroundColor: msg.avatar_color }}
      >
        {(msg.sender_name || '?')[0]}
      </div>
      {msg.sender_type === 'bot' && (
        <div className="absolute -bottom-1 -right-1 bg-gray-900 rounded-full w-4 h-4 flex items-center justify-center text-[10px] border border-gray-700 shadow-sm">
          🤖
        </div>
      )}
    </div>
  )

  if (isTyping) {
    return (
      <div className="flex items-start gap-2 py-1">
        {avatar}
        <div>
          <div className="text-xs text-gray-400 mb-1">{msg.sender_name}</div>
          <div className="bg-gray-800 rounded-2xl px-4 py-2 inline-flex gap-1 items-center">
            {[0, 150, 300].map((delay) => (
              <span key={delay} className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: `${delay}ms` }} />
            ))}
          </div>
        </div>
      </div>
    )
  }

  // 工作流人确认门：内联卡片（不是弹窗），人点确认才推进；想改就直接发消息。
  if (msg.meta?.kind === 'confirm_gate') {
    const status = gateState || (msg.meta.status === 'confirmed' ? 'confirmed' : 'pending')
    return (
      <div className="flex items-start gap-2 py-1">
        {avatar}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-medium text-gray-200">{msg.sender_name}</span>
            <span className="text-xs text-indigo-400 bg-indigo-950/50 rounded px-1 py-0.5">需确认</span>
            <span className="text-xs text-gray-500">
              {msg.created_at ? new Date(msg.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}
            </span>
          </div>
          <div className="border border-indigo-500/40 bg-indigo-950/20 rounded-xl px-4 py-3 inline-block max-w-lg">
            <div className="text-sm text-gray-200 mb-2">{msg.content}</div>
            {status === 'confirmed' ? (
              <div className="text-sm text-green-400">✅ 已确认</div>
            ) : status === 'revising' ? (
              <div className="text-sm text-gray-400">✏️ 已选择修改 —— 直接发消息调整即可</div>
            ) : (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { onConfirmGate?.(msg.meta.gate_id); setGateState('confirmed') }}
                  className="px-3 py-1 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition-colors"
                >✅ 确认</button>
                <button
                  onClick={() => setGateState('revising')}
                  className="px-3 py-1 rounded-lg border border-gray-600 text-gray-300 hover:bg-gray-700 text-sm transition-colors"
                >✏️ 修改</button>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`group flex items-start gap-2 py-1 relative rounded-lg transition-colors duration-300 ${highlighted ? 'bg-indigo-500/10' : ''}`}>
      {avatar}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className="text-sm font-medium text-gray-200">{msg.sender_name}</span>
          {msg.is_auto_reply && <span className="text-xs text-indigo-400 bg-indigo-950/50 rounded px-1 py-0.5">↩ 自动回复</span>}
          <span className="text-xs text-gray-500">
            {msg.created_at ? new Date(msg.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}
          </span>
          {(msg.input_tokens || msg.output_tokens) && (
            <span className="text-[10px] text-gray-600 hover:text-gray-400 cursor-default transition-colors" title={`输入 ${msg.input_tokens ?? 0} tokens · 输出 ${msg.output_tokens ?? 0} tokens`}>
              {((msg.input_tokens ?? 0) + (msg.output_tokens ?? 0)).toLocaleString()}t
            </span>
          )}
          {isPinned && <span className="text-xs text-yellow-600 ml-1">📌</span>}
          {!msg.streaming && !msg.is_deleted && (
            <span className={`${showEmojiPicker ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} flex items-center gap-1 transition-all relative`}>
              {QUICK_EMOJIS.map(e => (
                <button key={e} onClick={() => onReact?.(e)} className="text-sm hover:scale-125 transition-transform leading-none">{e}</button>
              ))}
              <button
                onClick={(e) => { e.stopPropagation(); setShowEmojiPicker(p => !p) }}
                onMouseDown={(e) => e.stopPropagation()}
                className="text-xs text-gray-500 hover:text-gray-300 leading-none px-0.5"
                title="更多表情"
              >＋</button>
              {showEmojiPicker && (
                <div className="absolute top-6 left-0 z-50">
                  <EmojiPicker onSelect={(emoji) => { onReact?.(emoji) }} onClose={closeEmojiPicker} />
                </div>
              )}
              <span className="w-px h-3 bg-gray-700 mx-1" />
              {onReply && (
                <button onClick={() => onReply(msg)} className="text-xs text-gray-500 hover:text-indigo-400">↩ 回复</button>
              )}
              {msg.id && (isPinned
                ? <button onClick={() => onUnpin?.(msg.id)} className="text-xs text-yellow-600 hover:text-yellow-400">取消置顶</button>
                : <button onClick={() => onPin?.(msg.id)} className="text-xs text-gray-500 hover:text-yellow-400">📌 置顶</button>
              )}
              {isOwn && (
                <>
                  <button onClick={() => { setEditing(true); setEditText(msg.content) }} className="text-xs text-gray-500 hover:text-yellow-400">编辑</button>
                  <button onClick={handleWithdraw} className="text-xs text-gray-500 hover:text-red-400">撤回</button>
                </>
              )}
            </span>
          )}
        </div>
        {msg.reply_to && (
          <div className="flex items-start gap-1.5 mb-1.5 pl-2 border-l-2 border-gray-600">
            <div className="min-w-0">
              <span className="text-xs text-indigo-400 font-medium">{msg.reply_to.sender_name}</span>
              <p className="text-xs text-gray-500 truncate">{msg.reply_to.content.slice(0, 80)}{msg.reply_to.content.length > 80 ? '...' : ''}</p>
            </div>
          </div>
        )}
        {msg.skills_loaded?.length > 0 && (
          <SkillsStrip skills={msg.skills_loaded} />
        )}
        {msg.is_deleted ? (
          <p className="text-sm text-gray-500 italic">此消息已撤回</p>
        ) : editing ? (
          <div className="flex flex-col gap-1.5">
            <textarea
              ref={editRef}
              value={editText}
              onChange={e => setEditText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); handleEditSave() }
                if (e.key === 'Escape') setEditing(false)
              }}
              className="w-full bg-gray-700 text-gray-100 text-sm rounded-lg px-3 py-2 resize-none outline-none focus:ring-1 focus:ring-indigo-500"
              rows={3}
            />
            <div className="flex gap-2 text-xs">
              <button onClick={handleEditSave} className="text-indigo-400 hover:text-indigo-300">保存</button>
              <button onClick={() => setEditing(false)} className="text-gray-500 hover:text-gray-300">取消</button>
            </div>
          </div>
        ) : msg.streaming ? (
          <div className="text-sm text-gray-100 whitespace-pre-wrap break-words leading-relaxed">
            {stripSentinels(msg.content)}
            <span className="inline-block w-0.5 h-4 bg-gray-300 animate-pulse ml-px align-text-bottom rounded-sm" />
          </div>
        ) : (
          <div>
            {msg.content && (
              <div className="text-sm text-gray-100 leading-relaxed prose prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {stripSentinels(msg.content)}
                </ReactMarkdown>
              </div>
            )}
            {msg.file_url && msg.file_type?.startsWith('image/') && (
              <img
                src={msg.file_url}
                alt={msg.file_name || '图片'}
                className="mt-1.5 max-w-xs max-h-64 rounded-lg object-contain cursor-zoom-in hover:opacity-90 transition-opacity"
                onClick={() => setLightboxSrc(msg.file_url)}
              />
            )}
            {msg.file_url && !msg.file_type?.startsWith('image/') && (
              <a
                href={msg.file_url}
                target="_blank"
                rel="noreferrer"
                download={msg.file_name}
                className="mt-1.5 flex items-center gap-2 bg-gray-800 hover:bg-gray-750 border border-gray-700 rounded-lg px-3 py-2 max-w-xs transition-colors no-underline"
              >
                <span className="text-xl flex-shrink-0">{
                  msg.file_type?.includes('word') ? '📝' :
                  msg.file_type?.includes('sheet') || msg.file_type?.includes('excel') ? '📊' :
                  msg.file_type === 'application/pdf' ? '📕' :
                  '📄'
                }</span>
                <div className="min-w-0">
                  <div className="text-xs text-gray-200 truncate">{msg.file_name}</div>
                  <div className="text-xs text-gray-500">{msg.file_size ? `${(msg.file_size / 1024).toFixed(1)} KB` : ''}</div>
                </div>
                <span className="text-xs text-indigo-400 flex-shrink-0 ml-auto">下载</span>
              </a>
            )}
          </div>
        )}
        {msg.edited && !msg.is_deleted && (
          <span className="text-xs text-gray-600 mt-0.5 block">（已编辑）</span>
        )}
        {Object.keys(reactions).length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {Object.entries(reactions).map(([emoji, memberIds]) => {
              const reacted = memberIds.includes(currentMemberId)
              return (
                <button
                  key={emoji}
                  onClick={() => onReact?.(emoji)}
                  className={`flex items-center gap-1 text-xs rounded-full px-2 py-0.5 border transition-colors ${
                    reacted
                      ? 'bg-indigo-900/60 border-indigo-500 text-indigo-300'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500'
                  }`}
                >
                  <span>{emoji}</span>
                  <span>{memberIds.length}</span>
                </button>
              )
            })}
          </div>
        )}
        {msg.member_id === currentMemberId && msg.id && (() => {
          const readers = members.filter(m => m.id !== currentMemberId && (readMap[m.id] || 0) >= msg.id)
          return readers.length > 0 ? (
            <div className="text-xs text-gray-500 mt-0.5">
              已读 {readers.map(r => r.name).join('、')}
            </div>
          ) : null
        })()}
      </div>
      {lightboxSrc && <Lightbox src={lightboxSrc} onClose={() => setLightboxSrc(null)} />}
    </div>
  )
}
