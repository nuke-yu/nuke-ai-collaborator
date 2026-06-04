# Nuke AI Collaborator - Dynamic Theme & Skin System

We have successfully redesigned the project's layout and added a **Dynamic Theme Switching System**. This enables you to dynamically change the chat UI skin at any time.

The system is fully compatible with the existing Tailwind CSS v4 utility classes and updates color and font rules on-the-fly without rebuilding.

---

## 🎨 Theme Options

We designed **six distinct, highly polished themes** inside the system, heavily inspired by the visual elements of the pages you provided:

| Theme Name | Description | Key Visual Elements | Reference & Inspiration |
| :--- | :--- | :--- | :--- |
| **🌙 默认暗黑 (Default Dark)** | The classic deep slate mode | Deep gray backgrounds, crisp white text, indigo accents. | Standard professional chat client. |
| **☀️ 极简明亮 (Elegant Light)** | Soft and clean light mode | Off-white surfaces, dark slate text, subtle grey borders, indigo accents. | Modern productivity tools (Linear/Notion). |
| **🎙️ ElevenLabs** | Premium ultra-polished dark theme | Warm charcoal backgrounds (`#0f0f0e`), rounded styling, elegant Outfit typography, and fresh aquamarine/mint green highlights (`#10b981`). | [ElevenLabs Dubbing App](https://elevenlabs.io/app/dubbing) |
| **🏦 HSBC 商务 (HSBC Corporate)** | Clean, corporate presentation theme | Clean white backgrounds, crisp Barlow font, structured grids, sharp square corners, primary corporate red accents (`#DB0011`). | [OIS April Review Page](file:///Users/Nuke/claudeFolder/ois-april-review.html) |
| **👾 赛博霓虹 (Cyberpunk)** | Retro-futuristic synthwave theme | JetBrains Mono fonts, neon-purple background, glowing neon-pink elements, glowing neon-cyan badges and links. | Retro gaming & synthwave dashboard. |
| **🔮 毛玻璃幻彩 (Glassmorphism)** | Dreamy transparent glass look | Frosted glass backgrounds with backdrop-blur, custom transparency, and an animated color-shifting gradient canvas in the background. | Premium OS designs (macOS / Windows 11 Fluent). |

---

## ⚙️ Technical Architecture

### 1. Tailwind CSS v4 Variable Mapping
In Tailwind CSS v4, all color utility classes are compiled into CSS custom properties (e.g. `bg-gray-900` points to `var(--color-gray-900)`). 

Instead of rewriting the entire codebase's utility classes (like `bg-gray-900` or `text-gray-100`), we **dynamically remap these variables** inside custom HTML selector classes in `index.css`:

```css
/* HSBC Theme Example */
html.theme-hsbc {
  font-family: 'Barlow', 'Inter', sans-serif !important;
  --color-gray-950: #ffffff; /* maps dark backgrounds to white */
  --color-gray-900: #ffffff;
  --color-gray-800: #f5f5f7; /* maps sidebar to soft grey */
  --color-gray-300: #1a1a1a; /* maps text colors to dark grey */
  --color-indigo-600: #db0011; /* remaps primary indigo highlights to HSBC Red */
  ...
}
```

This trick allows us to achieve 100% theme coverage across all 22 components in a fraction of a second.

### 2. State & Props Flow
* **App.jsx**: Stores the current active theme in `localStorage` (`app-theme`) and updates the `<html>` root class list (e.g. adding `theme-elevenlabs`).
* **ChatWindow.jsx**: Proxies the theme state and event handler to the sidebar.
* **GroupList.jsx**: Renders a beautiful theme-selector dropdown menu (under the `🎨` button in the header) with a list of available themes, featuring smooth hover states and closing on click-outside.

---

## 🚀 How to Try It

1. Launch your development server (e.g., run `npm run dev` in the `frontend` folder if it is not already running).
2. Open the page in your browser.
3. In the sidebar header, click on the **🎨 Theme** button.
4. Select any theme from the dropdown (e.g. **HSBC 商务** or **ElevenLabs** or **毛玻璃幻彩**) to see the entire UI dynamically transform instantly!
