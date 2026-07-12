import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { theme } from 'ant-design-vue'

const { defaultAlgorithm, darkAlgorithm } = theme

export const THEMES = {
  stone: {
    name: '云灰蓝', label: 'Blue Gray', colorPrimary: '#59788C', primaryBg: 'rgba(89,120,140,0.10)',
    colorBgBase: '#F4F6F7', colorBgContainer: '#FFFFFF', sidebar: '#FBFCFC', sidebarText: '#4B5563',
    sidebarHover: '#F0F3F4', sidebarActive: '#4D6778', sidebarActiveBg: 'rgba(89,120,140,0.10)',
    sidebarBorder: '#E4E8EA', textColor: '#111827', subtleText: '#6B7280', borderColor: '#E4E8EA', dark: false,
  },
  sage: {
    name: '岩茶绿', label: 'Muted Green', colorPrimary: '#617C6A', primaryBg: 'rgba(97,124,106,0.10)',
    colorBgBase: '#F5F7F5', colorBgContainer: '#FFFFFF', sidebar: '#FBFCFB', sidebarText: '#4B5563',
    sidebarHover: '#F1F4F1', sidebarActive: '#506558', sidebarActiveBg: 'rgba(97,124,106,0.10)',
    sidebarBorder: '#E3E7E3', textColor: '#17201A', subtleText: '#6B7280', borderColor: '#E3E7E3', dark: false,
  },
  ink: {
    name: '岩墨灰', label: 'Slate', colorPrimary: '#687482', primaryBg: 'rgba(104,116,130,0.10)',
    colorBgBase: '#F5F6F7', colorBgContainer: '#FFFFFF', sidebar: '#FBFBFC', sidebarText: '#4B5563',
    sidebarHover: '#F1F3F4', sidebarActive: '#535E69', sidebarActiveBg: 'rgba(104,116,130,0.10)',
    sidebarBorder: '#E4E7EA', textColor: '#111827', subtleText: '#6B7280', borderColor: '#E4E7EA', dark: false,
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
  '--primary-bg':        'primaryBg',
}

export const useThemeStore = defineStore('theme', () => {
  const storedKey = localStorage.getItem('aiops_theme')
  const key = ref(THEMES[storedKey] ? storedKey : 'stone')
  const t = computed(() => THEMES[key.value] ?? THEMES.stone)

  function setTheme(newKey) {
    key.value = newKey
    localStorage.setItem('aiops_theme', newKey)
    _apply(THEMES[newKey] ?? THEMES.stone)
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
      colorText: t.value.textColor,
      colorTextSecondary: t.value.subtleText,
      colorBorder: t.value.borderColor,
      borderRadius: 10,
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    },
  }))

  _apply(t.value)

  return { key, t, setTheme, antTheme, THEMES }
})
