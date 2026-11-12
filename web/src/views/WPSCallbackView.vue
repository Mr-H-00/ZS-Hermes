<template>
  <main class="wps-callback">
    <div class="callback-card" aria-live="polite">
      <a-spin v-if="loading" size="large" />
      <CheckCircle2 v-else-if="connected" :size="36" class="success-icon" />
      <CircleAlert v-else :size="36" class="error-icon" />
      <h1>{{ loading ? '正在连接 WPS' : connected ? 'WPS 连接成功' : 'WPS 连接未完成' }}</h1>
      <p>{{ loading ? '正在确认你的 WPS 授权与身份' : resultMessage }}</p>
      <a-button v-if="!loading" type="primary" @click="router.replace('/workspace?tab=cloud')">进入我的云盘</a-button>
    </div>
  </main>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CheckCircle2, CircleAlert } from '@lucide/vue'
import { wpsCloudApi } from '@/apis/cloud_drive_api'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const connected = ref(false)
const resultMessage = ref('')

onMounted(async () => {
  const { code, state, error } = route.query
  // 先移除浏览器地址中的一次性凭据，失败后由云盘重新发起授权。
  await router.replace({ path: '/auth/wps/callback' })
  try {
    if (error || typeof code !== 'string' || typeof state !== 'string' || !code || !state) {
      resultMessage.value = '授权已取消或回调不完整，请在我的云盘重新连接。'
      return
    }
    const status = await wpsCloudApi.exchange(code, state)
    connected.value = status.connected
    resultMessage.value = `已连接 ${status.user?.name || '本人 WPS 云文档'}`
  } catch {
    resultMessage.value = '授权已过期或连接失败，请在我的云盘重新连接并检查应用权限。'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped lang="less">
.wps-callback { display: grid; place-items: center; min-height: 100vh; padding: 24px; background: var(--gray-25); }
.callback-card { width: min(100%, 460px); padding: 36px 24px; text-align: center; background: var(--gray-0); border: 1px solid var(--gray-150); border-radius: 8px; }
h1 { margin: 20px 0 10px; font-size: 20px; color: var(--gray-900); }
p { margin: 0 0 24px; font-size: 14px; color: var(--color-text-secondary); line-height: 1.6; }
.success-icon { color: var(--color-success-700); }
.error-icon { color: var(--color-warning-900); }
</style>
