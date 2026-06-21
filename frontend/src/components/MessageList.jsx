import { Fragment, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

const EMPTY_REACTIONS = {}
import MessageBubble from './MessageBubble'
import ThinkingSection from './ThinkingSection'
import ToolProgressBlock from './ToolProgressBlock'
import { useChatStore } from '../store/chatStore'
import { useGroupStore } from '../store/groupStore'

export default function MessageList({
  members,
  pins,
  highlightedId,
  group,
  loadingMore,
  scrollRef,
  bottomRef,
  onScroll,
  onReply,
  onReact,
  onPin,
  onUnpin,
  onConfirmGate,
  onReviseGate,
}) {
  const messages = useChatStore((s) => s.messages)
  const typing = useChatStore((s) => s.typing)
  const readMap = useChatStore((s) => s.readMap)
  const reactionMap = useChatStore((s) => s.reactionMap)
  const onlineSet = useChatStore((s) => s.onlineSet)
  const thoughtBlocks = useChatStore((s) => s.thoughtBlocks)
  const toolProgressBlocks = useChatStore((s) => s.toolProgressBlocks)
  const memberId = useGroupStore((s) => s.activeMemberId)
  const { t } = useTranslation()
  const pinsSet = useMemo(() => new Set(pins.map(p => p.id)), [pins])
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
            <p className="text-gray-500 text-sm">{t(K.messageList.groupStart, { name: group.name })}</p>
          </div>
          {members.length > 0 && (
            <div>
              <p className="text-xs text-gray-600 mb-3">{t(K.messageList.groupMembers)}</p>
              <div className="flex gap-3 justify-center flex-wrap">
                {members.map(m => (
                  <div key={m.id} className="flex flex-col items-center gap-1.5">
                    <div className="relative">
                      <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold text-white" style={{ backgroundColor: m.avatar_color }}>
                        {m.name[0]}
                      </div>
                      {(onlineSet.has(m.id) || m.type === 'bot') && <span className="absolute bottom-0 right-0 w-2.5 h-2.5 bg-green-400 rounded-full border-2 border-gray-900" />}
                    </div>
                    <span className="text-xs text-gray-500">{m.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="text-xs text-gray-600 mt-2">{t(K.messageList.sendToStart)}</p>
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
          if (msgDay === today) dateLabel = t(K.messageList.today)
          else if (msgDay === yesterday) dateLabel = t(K.messageList.yesterday)
          else dateLabel = new Date(msg.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
        }
        if (msg._compact_marker) {
          return (
            <div key={msg.id} className="flex items-center gap-3 py-2 my-2 px-4">
              <div className="flex-1 h-px bg-indigo-900/50" />
              <span className="text-[11px] text-indigo-400/70 flex-shrink-0 flex items-center gap-1.5">
                <span>⚡</span>
                <span>{msg.message || t(K.messageList.contextCompressed)}</span>
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
            {msg.thought_id && thoughtBlocks[msg.thought_id] && (
              <ThinkingSection blocks={thoughtBlocks[msg.thought_id]} streaming={msg.streaming} />
            )}
            {/* 暂时隐藏聊天窗里的 run 工具调用命令 + iteration 显示（仅保留上方 Thinking）。
                代码保留备用，需要恢复时删除本注释包裹即可。
            {msg.temp_id && toolProgressBlocks && Object.entries(toolProgressBlocks)
              .filter(([key]) => key.startsWith(`${msg.temp_id}-`))
              .map(([key, tool]) => (
                <div key={`tool-${key}`} className="px-4">
                  <ToolProgressBlock
                    tempId={msg.temp_id}
                    toolName={tool.tool_name}
                    args={tool.args}
                    iteration={tool.iteration}
                    durationSec={tool.duration_sec}
                    result={tool.result}
                    isError={tool.is_error}
                  />
                </div>
              ))}
            */}
            <div data-msg-id={msg.id}>
              <MessageBubble
                msg={msg}
                currentMemberId={memberId}
                members={members}
                readMap={readMap}
                onReply={onReply}
                reactions={reactionMap[String(msg.id)] || EMPTY_REACTIONS}
                onReact={(emoji) => onReact(msg.id, emoji)}
                isPinned={pinsSet.has(msg.id)}
                onPin={(id) => onPin(id)}
                onUnpin={(id) => onUnpin(id)}
                highlighted={msg.id === highlightedId}
                onConfirmGate={onConfirmGate}
                onReviseGate={onReviseGate}
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
