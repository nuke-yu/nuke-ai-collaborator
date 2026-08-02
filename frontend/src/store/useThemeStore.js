import { create } from 'zustand';

export const THEMES = [
  // openhanako 经典皮肤
  { id: 'openhana-midnight', name: '青夜沉思 (Midnight)', icon: '🌃', category: 'openhanako' },
  { id: 'openhana-warm-paper', name: '暖纸书香 (Warm Paper)', icon: '📜', category: 'openhanako' },
  { id: 'openhana-grass-aroma', name: '草木芳华 (Grass)', icon: '🌿', category: 'openhanako' },
  { id: 'openhana-coral', name: '珊瑚落日 (Coral)', icon: '🪸', category: 'openhanako' },
  
  // 原项目特色皮肤
  { id: 'elevenlabs', name: 'ElevenLabs 极客蓝', icon: '🎙️', category: 'Original' },
  { id: 'hsbc', name: 'HSBC 商务红', icon: '🏦', category: 'Original' },
  { id: 'glass', name: 'Glass 磨砂水晶', icon: '🔮', category: 'Original' },

  // shadcn/ui & 社区流行皮肤
  { id: 'dark-obsidian', name: '深空黑曜石 (Obsidian)', icon: '🌙', category: 'shadcn/ui' },
  { id: 'shadcn-violet', name: '霓虹紫夜 (Violet)', icon: '🟣', category: 'shadcn/ui' },
  { id: 'shadcn-zinc', name: '极客中性灰 (Zinc)', icon: '💻', category: 'shadcn/ui' },
  { id: 'catppuccin-mocha', name: '摩卡摩卡 (Catppuccin)', icon: '☕', category: 'Community' },
  { id: 'cyberpunk', name: '赛博霓虹 (Cyberpunk)', icon: '👾', category: 'Community' },
  { id: 'clean-daylight', name: '雅致日光白 (Daylight)', icon: '☀️', category: 'Light' },
];

const getInitialTheme = () => {
  const saved = localStorage.getItem('app-theme');
  if (saved && THEMES.some(t => t.id === saved)) {
    return saved;
  }
  return 'dark-obsidian';
};

const applyThemeToDOM = (themeId) => {
  document.documentElement.setAttribute('data-theme', themeId);
  
  // Safely clean up ALL previous theme-xxx classes to prevent style collisions
  Array.from(document.documentElement.classList)
    .filter(cls => cls.startsWith('theme-'))
    .forEach(cls => document.documentElement.classList.remove(cls));

  document.documentElement.classList.add(`theme-${themeId}`);

  const isLight = themeId === 'clean-daylight' || themeId === 'hsbc' || themeId === 'openhana-warm-paper' || themeId === 'elegant-light';
  document.documentElement.classList.toggle('light', isLight);
};

// Initialize immediately on load
const initialTheme = getInitialTheme();
applyThemeToDOM(initialTheme);

export const useThemeStore = create((set) => ({
  theme: initialTheme,
  themes: THEMES,
  setTheme: (newTheme) => {
    localStorage.setItem('app-theme', newTheme);
    applyThemeToDOM(newTheme);
    set({ theme: newTheme });
  },
}));
