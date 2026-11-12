import assert from 'node:assert/strict'
import test from 'node:test'
import { ref } from 'vue'
import { createPinia, disposePinia } from 'pinia'
import { createTestServer } from '../helpers/createTestServer.js'

/** 使用真实响应式 Store，隔离浏览器偏好与持久化边界。 */
async function fixture(saved = null, preferredDark = false) {
  const systemDark = ref(preferredDark)
  const storage = new Map(saved === null ? [] : [['theme', saved]])
  const classes = new Set()
  const document = { documentElement: { classList: {
    add: (name) => classes.add(name),
    remove: (name) => classes.delete(name)
  } } }
  globalThis.localStorage = { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) }
  globalThis.document = document
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => classes.has('dark') ? '#222222' : '#eeeeee' })
  globalThis.__themeTest = { systemDark }
  const server = await createTestServer({
    configFile: false,
    ssr: { noExternal: ['@vueuse/core', 'ant-design-vue'] },
    server: { middlewareMode: true, hmr: false },
    plugins: [{
      name: 'theme-boundaries', enforce: 'pre',
      resolveId(id) {
        if (id === '@vueuse/core' || id === 'ant-design-vue') return `\0theme-test:${id}`
      },
      load(id) {
        if (id === '\0theme-test:@vueuse/core') return 'export const usePreferredDark = () => globalThis.__themeTest.systemDark'
        if (id === '\0theme-test:ant-design-vue') return "export const theme = { darkAlgorithm: 'dark', defaultAlgorithm: 'light' }"
      }
    }]
  })
  const { useThemeStore } = await server.ssrLoadModule('/src/shared/model/theme.js')
  const pinia = createPinia()
  const store = useThemeStore(pinia)
  return { store, systemDark, storage, classes, dispose: async () => { disposePinia(pinia); await server.close(); delete globalThis.__themeTest; delete globalThis.localStorage; delete globalThis.document; delete globalThis.getComputedStyle } }
}

test('主题按 light、dark、auto 轮换并保存选择，自动模式实时更新根节点和控件', async () => {
  const f = await fixture()
  try {
    assert.equal(f.store.themeMode, 'light')
    for (const mode of ['dark', 'auto', 'light']) {
      f.store.toggleTheme()
      assert.equal(f.store.themeMode, mode)
      assert.equal(f.storage.get('theme'), mode)
    }
    f.store.setTheme('auto')
    for (const dark of [true, false, true]) {
      f.systemDark.value = dark
      assert.equal(f.store.isDark, dark)
      assert.equal(f.classes.has('dark'), dark)
      assert.equal(f.store.currentTheme.algorithm, dark ? 'dark' : 'light')
      assert.equal(f.store.currentTheme.token.colorPrimary, dark ? '#222222' : '#eeeeee')
      assert.equal(f.storage.get('theme'), 'auto')
    }
    f.store.setTheme(false)
    f.systemDark.value = false
    f.systemDark.value = true
    assert.equal(f.store.isDark, false)
    assert.equal(f.storage.get('theme'), 'light')
    f.store.setTheme(true)
    f.systemDark.value = false
    assert.equal(f.store.isDark, true)
    assert.equal(f.storage.get('theme'), 'dark')
  } finally {
    await f.dispose()
  }
})

test('重载保留三种选择，默认浅色，auto 初始化解析浏览器偏好', async () => {
  for (const saved of [null, 'invalid', 'light', 'dark', 'auto']) {
    for (const preferred of [true, false]) {
      const f = await fixture(saved, preferred)
      try {
        const dark = saved === 'dark' || (saved === 'auto' && preferred)
        assert.equal(f.store.themeMode, ['light', 'dark', 'auto'].includes(saved) ? saved : 'light')
        assert.equal(f.store.isDark, dark)
        assert.equal(f.classes.has('dark'), dark)
        assert.equal(f.store.currentTheme.algorithm, dark ? 'dark' : 'light')
      } finally {
        await f.dispose()
      }
    }
  }
})
