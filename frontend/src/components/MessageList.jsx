import { Fragment } from 'react'
import MessageBubble from './MessageBubble'

export default function MessageList({
  messages,
  typing,
  memberId,
  members,
  readMap,
  reactionMap,
  pins,
  highlightedId,
  group,
  onlineSet,
  loadingMore,
  scrollRef,
  bottomRef,
  onScroll,
  onReply,
  onReact,
  onPin,
  onUnpin,
  onConfirmGate
}) {
  return (
    <div
      ref={scrollRef}
      className="flex-1 overflow-y-auto px-4 py-4 space-y-1 pb-14 md:pb-4"
      onScroll={onScroll}
    >
      {loadingMore && (
        <div className="text-center py-3">
          <span className="inline-block w-4 h-4 border-2 border-gray-600 border-t-gray-400 rounded-full animate-spin" />
        </div>
      )}
      {messages.length === 0 && !loadingMore && group && (
        <div className="flex-1 flex flex-col items-center justify-center gap-5 py-16 text-center select-none">
          <div className="text-5xl">💬</div>
          <div>
            <h3 className="text-gray-200 font-semibold text-base mb-1"># {group.name}</h3>
            <p className="text-gray-500 text-sm">这是 <span className="text-indigo-400 font-medium">{group.name}</span> 的开始</p>
          </div>
          {members.length > 0 && (
            <div>
              <p className="text-xs text-gray-600 mb-3">群组成员</p>
              <div className="flex gap-3 justify-center flex-wrap">
                {members.map(m => (
                  <div key={m.id} className="flex flex-col items-center gap-1.5">
                    <div className="relative">
                      <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-white" style={{ backgroundColor: m.avatar_color }}>
                        {m.name[0]}
                      </div>
                      {onlineSet.has(m.id) && <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-green-400 rounded-full border-2 border-gray-900" />}
                    </div>
                    <span className="text-xs text-gray-500">{m.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="text-xs text-gray-600 mt-2">发送消息开始对话 👇</p>
        </div>
      )}
      {messages.map((msg, i) => {
        const msgDay = msg.created_at ? new Date(msg.created_at).toDateString() : null
        const prevDay = i > 0 && messages[i - 1].created_at ? new Date(messages[i - 1].created_at).toDateString() : null
        const showDate = msgDay && msgDay !== prevDay
        let dateLabel = ''
        if (showDate) {
          const today = new Date().toDateString()
          const d = new Date(); d.setDate(d.getDate() - 1); const yesterday = d.toDateString()
          if (msgDay === today) dateLabel = '今天'
          else if (msgDay === yesterday) dateLabel = '昨天'
          else dateLabel = new Date(msg.created_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' })
        }
        if (msg._compact_marker) {
          return (
            <div key={msg.id} className="flex items-center gap-3 py-2 my-2 px-4">
              <div className="flex-1 h-px bg-indigo-900/50" />
              <span className="text-[11px] text-indigo-400/70 flex-shrink-0 flex items-center gap-1.5">
                <span>⚡</span>
                <span>{msg.message || '上下文已压缩'}</span>
              </span>
              <div className="flex-1 h-px bg-indigo-900/50" />
            </div>
          )
        }
        return (
          <Fragment key={msg.id ?? msg.temp_id}>
            {showDate && (
              <div className="flex items-center gap-3 py-2 my-1">
                <div className="flex-1 h-px bg-gray-800" />
                <span className="text-xs text-gray-500 flex-shrink-0">{dateLabel}</span>
                <div className="flex-1 h-px bg-gray-800" />
              </div>
            )}
            <div data-msg-id={msg.id}>
              <MessageBubble
                msg={msg}
                currentMemberId={memberId}
                members={members}
                readMap={readMap}
                onReply={onReply}
                reactions={reactionMap[String(msg.id)] || {}}
                onReact={(emoji) => onReact(msg.id, emoji)}
                isPinned={pins.some(p => p.id === msg.id)}
                onPin={(id) => onPin(id)}
                onUnpin={(id) => onUnpin(id)}
                highlighted={msg.id === highlightedId}
                onConfirmGate={onConfirmGate}
              />
            </div>
          </Fragment>
        )
      })}
      {typing && <MessageBubble msg={typing} isTyping />}
      <div ref={bottomRef} />
    </div>
  )
}
