import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { theme } from 'ant-design-vue'

const { defaultAlgorithm, darkAlgorithm } = theme

export const THEMES = {
  amber: {
    name: '琥珀', label: '暖金',
    colorPrimary: '#D97706',
    colorBgBase: '#FEFCF7',
    colorBgContainer: '#FFFFFF',
    sidebar: '#1A1206',
    sidebarText: '#F5DEB3',
    sidebarHover: '#2D1E0A',
    sidebarActive: '#FBBF24',
    sidebarActiveBg: 'rgba(251,191,36,0.13)',
    sidebarBorder: 'rgba(255,255,255,0.06)',
    textColor: '#1A1206',
    subtleText: '#78674A',
    borderColor: '#F0E4C8',
    dark: false,
  },
  rose: {
    name: '玫瑰', label: '暖粉',
    colorPrimary: '#E11D48',
    colorBgBase: '#FFF9FA',
    colorBgContainer: '#FFFFFF',
    sidebar: '#1A0810',
    sidebarText: '#FECDD3',
    sidebarHover: '#2D1020',
    sidebarActive: '#FB7185',
    sidebarActiveBg: 'rgba(251,113,133,0.13)',
    sidebarBorder: 'rgba(255,255,255,0.06)',
    textColor: '#1A0810',
    subtleText: '#9F485E',
    borderColor: '#FCE4EB',
    dark: false,
  },
  ember: {
    name: '余烬', label: '暖焰',
    colorPrimary: '#C2410C',
    colorBgBase: '#FEF7F2',
    colorBgContainer: '#FFFFFF',
    sidebar: '#1A0D08',
    sidebarText: '#FED7AA',
    sidebarHover: '#2D1810',
    sidebarActive: '#F97316',
    sidebarActiveBg: 'rgba(249,115,22,0.13)',
    sidebarBorder: 'rgba(255,255,255,0.06)',
    textColor: '#1A0D08',
    subtleText: '#9A4A25',
    borderColor: '#FDE4D0',
    dark: false,
  },
  ocean: {
    name: '海洋', label: '冷蓝',
    colorPrimary: '#0284C7',
    colorBgBase: '#F0F9FF',
    colorBgContainer: '#FFFFFF',
    sidebar: '#0C4A6E',
    sidebarText: '#BAE6FD',
    sidebarHover: '#075985',
    sidebarActive: '#38BDF8',
    sidebarActiveBg: 'rgba(56,189,248,0.13)',
    sidebarBorder: 'rgba(255,255,255,0.08)',
    textColor: '#0C4A6E',
    subtleText: '#0369A1',
    borderColor: '#BAE6FD',
    dark: false,
  },
}

const CSS_VARS = {
  '--sidebar-bg':        'sidebar',
  '--sidebar-text':      'sidebarText',
  '--sidebar-hover':     'sidebarHover',
  '--sidebar-active':    'sidebarActive',
  '--sidebar-active-bg': 'sidebarActiveBg',
  '--sidebar-border':    'sidebarBorder',
  '--body-bg':           'colorBgBase',
  '--card-bg':           'colorBgContainer',
  '--border-color':      'borderColor',
  '--text':              'textColor',
  '--text-subtle':       'subtleText',
  '--primary':           'colorPrimary',
}

export const useThemeStore = defineStore('theme', () => {
  const key = ref(localStorage.getItem('aiops_theme') || 'amber')
  const t = computed(() => THEMES[key.value] ?? THEMES.amber)

  function setTheme(newKey) {
    key.value = newKey
    localStorage.setItem('aiops_theme', newKey)
    _apply(THEMES[newKey] ?? THEMES.amber)
  }

  function _apply(thm) {
    const root = document.documentElement
    Object.entries(CSS_VARS).forEach(([cssVar, prop]) =>
      root.style.setProperty(cssVar, thm[prop])
    )
  }

  const antTheme = computed(() => ({
    algorithm: t.value.dark ? darkAlgorithm : defaultAlgorithm,
    token: {
      colorPrimary: t.value.colorPrimary,
      colorBgBase: t.value.colorBgBase,
      colorBgContainer: t.value.colorBgContainer,
      borderRadius: 8,
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    },
  }))

  _apply(t.value)

  return { key, t, setTheme, antTheme, THEMES }
})
