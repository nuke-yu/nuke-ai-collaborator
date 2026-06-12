import { useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { K } from '../i18n/keys'

const CATEGORY_KEYS = [
  {
    label: '😀', nameKey: K.emojiPicker.catFaces,
    emojis: ['😀','😁','😂','🤣','😃','😄','😅','😆','😉','😊','😋','😎','😍','🥰','😘','🥲','😐','😑','😶','🙄','😏','😒','🥺','😢','😭','😤','😠','😡','🤬','🤯','😳','🥵','🥶','😱','😨','😰','😥','😓','🤗','🫡','🤔','🤫','🤭','😷','🤒','🤕','🥴','😵','🤑','😴','😪','🥱'],
  },
  {
    label: '👋', nameKey: K.emojiPicker.catGestures,
    emojis: ['👍','👎','👏','🙌','🤝','🤜','🤛','👊','✊','✌️','🤞','🫰','🤟','🤘','👌','🤌','🤏','🫳','🫴','☝️','👆','👇','👈','👉','🖐️','✋','🤚','👋','🤙','💪','🦾','🙏','🫶','🤲','👐'],
  },
  {
    label: '🎉', nameKey: K.emojiPicker.catCelebration,
    emojis: ['🎉','🎊','🎈','🎁','🏆','🥇','🥈','🥉','🏅','🎖️','🥂','🍾','🎂','🎆','🎇','✨','🌟','💫','⭐','🌈','🎯','🎮','🎨','🎭','🎪','🎬','🎤','🎵','🎶','🎸','🥳','🎠','🎡','🎢','🎰'],
  },
  {
    label: '🐶', nameKey: K.emojiPicker.catAnimals,
    emojis: ['🐶','🐱','🐭','🐹','🐰','🦊','🐻','🐼','🐨','🐯','🦁','🐮','🐷','🐸','🐙','🐠','🐡','🐬','🦋','🐝','🦉','🦄','🐲','🦕','🐊','🦈','🦭','🐳','🦒','🦓','🦏','🐘','🦛','🐪','🦘'],
  },
  {
    label: '🍎', nameKey: K.emojiPicker.catFood,
    emojis: ['🍎','🍊','🍋','🍇','🍓','🥝','🍑','🥭','🍍','🥥','🥑','🍆','🥕','🌽','🍕','🍔','🌮','🌯','🥗','🍜','🍣','🍱','🍛','🍦','🎂','🍰','🍩','🍪','☕','🍺','🥤','🧃','🥂','🍾','🧋'],
  },
  {
    label: '❤️', nameKey: K.emojiPicker.catSymbols,
    emojis: ['❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❤️‍🔥','💯','🔥','💥','💢','💤','💬','💭','🗯️','✅','❌','⚠️','🚫','💡','🔑','🗝️','🔒','🔓','🚀','🛸','⚡','🌊','🌀','🌙','☀️'],
  },
]

export default function EmojiPicker({ onSelect, onClose }) {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState(0)
  const ref = useRef(null)

  const CATEGORIES = CATEGORY_KEYS.map(cat => ({ ...cat, name: t(cat.nameKey) }))

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose() }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  return (
    <div ref={ref} className="bg-gray-800 border border-gray-700 rounded-xl shadow-xl w-64 overflow-hidden">
      <div className="flex border-b border-gray-700">
        {CATEGORIES.map((cat, i) => (
          <button
            key={i}
            onClick={() => setActiveTab(i)}
            title={cat.name}
            className={`flex-1 py-1.5 text-base transition-colors ${activeTab === i ? 'bg-gray-700' : 'hover:bg-gray-750'}`}
          >
            {cat.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-8 p-1 max-h-52 overflow-y-auto">
        {CATEGORIES[activeTab].emojis.map((emoji, i) => (
          <button
            key={i}
            onClick={() => { onSelect(emoji); onClose() }}
            className="text-lg p-1 hover:bg-gray-700 rounded transition-colors leading-none"
          >
            {emoji}
          </button>
        ))}
      </div>
      <div className="px-2 py-1 border-t border-gray-700 text-xs text-gray-600 text-center">{CATEGORIES[activeTab].name}</div>
    </div>
  )
}
