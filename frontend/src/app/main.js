import { createApp, h } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'

import App from './App.vue'
import router from './router/index.js'

import Antd, { Spin } from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'
import '@/assets/css/main.css'
import AntEmpty from '@/shared/ui/AntEmpty'

Spin.setDefaultIndicator({
  indicator: () =>
    h(
      'div',
      { class: 'yuxi-loading', 'aria-hidden': 'true' },
      Array.from({ length: 5 }, () => h('div'))
    )
})

const app = createApp(App)
const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

app.use(pinia)
app.use(router)
app.use(Antd)
app.component('a-empty', AntEmpty)

// 预加载信息配置
import { useInfoStore } from '@/modules/settings/model/info'
const infoStore = useInfoStore()
infoStore.loadInfoConfig()

app.mount('#app')
