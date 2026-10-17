import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', 'dist-prod/**', 'node_modules/**', 'public/**'] },
  ...pluginVue.configs['flat/essential'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
    },
    rules: {
      // 真实缺陷类型：对象字面量重复键（曾在前端出现过 totalTokens 重复定义）
      'no-dupe-keys': 'error',
      'no-unused-vars': ['warn', { args: 'none', varsIgnorePattern: '^_' }],
      // 已知技术债：NotifyConfigCard 直接修改父级传入的草稿对象（应改为
      // draft + emit('save', payload)）。先降级为 warn，让 CI 继续兜住 dupe-keys 等真错误。
      'vue/no-mutating-props': 'warn',
      // 模板里使用 kebab-case 组件名是项目现状，不强制改名
      'vue/multi-word-component-names': 'off',
    },
  },
]
