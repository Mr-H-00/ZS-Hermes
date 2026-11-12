<template>
  <section class="cloud-drive-settings">
    <h3 class="section-title">WPS 365 云文档</h3>
    <a-spin :spinning="loading">
      <div class="wps-settings-panel">
        <p class="section-description">
          管理员配置 WPS 365 自建应用。每位用户在下方完成本人授权。
        </p>
        <a-alert v-if="loadError" :message="loadError" type="error" show-icon />
        <template v-if="userStore.isAdmin && savedConfig">
          <a-form layout="vertical" class="wps-form" @finish="saveConfig">
            <a-form-item label="启用 WPS">
              <div class="enable-row">
                <a-switch v-model:checked="draft.enabled" aria-label="启用 WPS" :disabled="saving" />
                <span v-if="dirty" class="unsaved">有未保存的配置</span>
              </div>
            </a-form-item>
            <a-form-item label="应用 ID">
              <a-input v-model:value="draft.app_id" aria-label="WPS 应用 ID" :maxlength="255" :disabled="saving" />
            </a-form-item>
            <a-form-item label="应用密钥">
              <a-input-password
                v-model:value="draft.app_secret"
                aria-label="WPS 应用密钥"
                :placeholder="savedConfig.secret_configured ? '已配置；留空保留密钥' : '请输入应用密钥'"
                autocomplete="new-password"
                :maxlength="2048"
                :disabled="saving || draft.clear_secret"
              />
            </a-form-item>
            <a-checkbox v-model:checked="draft.clear_secret" :disabled="saving" @change="draft.app_secret = ''">
              清除已保存密钥
            </a-checkbox>
            <a-form-item label="用户授权回调地址" class="callback-field">
              <div class="callback-input">
                <a-input v-model:value="draft.redirect_uri" aria-label="用户授权回调地址" placeholder="https://你的域名/auth/wps/callback" :maxlength="2048" :disabled="saving" />
                <a-button :disabled="!wpsCallbackUrl(draft.redirect_uri)" @click="copyCallback">复制</a-button>
              </div>
              <p class="field-help">使用公网 HTTPS 域名，完整地址必须与 WPS 后台登记值完全一致，包括协议、域名、端口和路径。</p>
              <a-alert v-if="draft.redirect_uri && !wpsCallbackUrl(draft.redirect_uri)" class="callback-notice" type="warning" show-icon message="此地址无法用于正式授权；请改用公网 HTTPS 域名，不能使用 localhost、IP 或 HTTP 地址。" />
              <p v-if="!defaultRedirectUri" class="field-help">本地测试请将本站通过 HTTPS 域名转发，并通过该域名登录后连接 WPS。回调域名须能打开此站点的回调页面。</p>
            </a-form-item>
            <div class="development-tunnel" aria-live="polite">
              <div class="development-heading">
                <div>
                  <h4>开发联调</h4>
                  <p class="field-help">启动已配置的固定命名 Tunnel，地址不会覆盖上面的正式回调地址。</p>
                </div>
                <a-tag v-if="tunnel.running" color="green">已启动</a-tag>
                <a-tag v-else-if="tunnel.state === 'starting'" color="processing">启动中</a-tag>
              </div>
              <a-alert v-if="tunnel.message" :message="tunnel.message" :type="tunnel.state === 'error' ? 'error' : 'info'" show-icon />
              <div v-if="tunnel.callback_url" class="callback-input tunnel-url">
                <a-input :value="tunnel.callback_url" readonly aria-label="开发联调回调地址" />
                <a-button @click="copyDevelopmentCallback">复制</a-button>
                <a-button @click="openDevelopmentCallback">打开</a-button>
              </div>
              <p v-if="tunnel.callback_url" class="field-help">请把这个固定回调地址登记到 WPS 开发者后台，并通过该地址打开本站后发起授权。</p>
              <div v-if="userStore.isAdmin" class="config-actions tunnel-actions">
                <a-button v-if="!tunnel.running" type="primary" :loading="startingTunnel || tunnel.state === 'starting'" :disabled="!tunnel.enabled || startingTunnel || stoppingTunnel || saving || tunnel.state === 'starting'" @click="startDevelopmentTunnel">
                  {{ tunnel.state === 'error' ? '重新启动联调' : '启动开发联调' }}
                </a-button>
                <a-button v-if="tunnel.running || tunnel.state === 'starting'" danger :loading="stoppingTunnel" :disabled="stoppingTunnel" @click="stopDevelopmentTunnel">
                  停止开发联调
                </a-button>
              </div>
            </div>
            <div class="config-actions">
              <a-button type="primary" html-type="submit" :loading="saving" :disabled="!dirty || loading">
                保存 WPS 配置
              </a-button>
              <a-button :disabled="saving" @click="load">重新读取</a-button>
            </div>
          </a-form>
        </template>
        <div v-else-if="loadError" class="config-actions"><a-button @click="load">重新读取</a-button></div>

        <a-alert v-if="originMismatch" class="callback-notice" type="warning" show-icon>
          <template #message>请通过已登记的域名访问并登录后连接：<a :href="authorizationOrigin + '/workspace?tab=cloud'">{{ authorizationOrigin }}</a></template>
        </a-alert>
        <p v-if="connection?.configured && !authorizationOrigin" class="field-help">请先启动开发联调并登记固定回调地址，再打开联调站点登录并连接。</p>

        <div class="connection-row">
          <div class="connection-info" aria-live="polite">
            <h4>本人连接</h4>
            <p v-if="connection?.connected" class="connected">
              <CheckCircle2 :size="15" /> 已连接 {{ connection.user?.name || 'WPS 用户' }}
            </p>
            <p v-else>{{ connection?.message || '正在读取连接状态' }}</p>
          </div>
          <div class="connection-actions">
            <a-button
              type="primary"
              :loading="connecting"
              :disabled="!connection?.enabled || !connection?.configured || !authorizationOrigin || originMismatch || dirty || loading || saving"
              @click="connect"
            >{{ connection?.connected ? '重新连接 WPS' : '连接 WPS' }}</a-button>
            <a-button :loading="checking" :disabled="loading || saving" @click="checkConnection">重新检查</a-button>
            <a-popconfirm v-if="connection?.connected" title="断开本人 WPS 连接？" ok-text="断开" cancel-text="取消" @confirm="disconnect">
              <a-button :loading="disconnecting">断开</a-button>
            </a-popconfirm>
          </div>
        </div>
        <p v-if="dirty" class="field-help">保存配置后再连接；修改应用配置后，所有用户需要重新授权。</p>
      </div>
    </a-spin>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircle2 } from '@lucide/vue'
import { useUserStore } from '@/stores/user'
import { wpsCloudApi } from '@/apis/cloud_drive_api'
import { wpsAuthorizationOrigin, wpsCallbackUrl } from '@/utils/cloudDrive'

const userStore = useUserStore()
const loading = ref(false)
const saving = ref(false)
const checking = ref(false)
const connecting = ref(false)
const disconnecting = ref(false)
const startingTunnel = ref(false)
const stoppingTunnel = ref(false)
const loadError = ref('')
const savedConfig = ref(null)
const connection = ref(null)
const tunnel = ref({ enabled: false, state: 'disabled', running: false, callback_url: '', configured_origin: '', message: '' })
const draft = reactive({ enabled: false, app_id: '', app_secret: '', clear_secret: false, redirect_uri: '' })
const defaultRedirectUri = computed(() => {
  if (window.location.hostname?.endsWith('.trycloudflare.com') || tunnel.value.configured_origin === window.location.origin) return ''
  return wpsCallbackUrl(`${window.location.origin}/auth/wps/callback`)
})
const authorizationOrigin = computed(() => wpsAuthorizationOrigin(connection.value, window.location.origin))
const originMismatch = computed(() => Boolean(authorizationOrigin.value && authorizationOrigin.value !== window.location.origin))
let pollTimer = null
let tunnelRequest = 0
let disposed = false
const dirty = computed(() => Boolean(savedConfig.value && (
  draft.enabled !== savedConfig.value.enabled ||
  draft.app_id.trim() !== savedConfig.value.app_id ||
  draft.redirect_uri.trim() !== (savedConfig.value.redirect_uri || defaultRedirectUri.value) || draft.app_secret || draft.clear_secret
)))

function applyConfig(config) {
  savedConfig.value = config
  Object.assign(draft, config, { app_secret: '', clear_secret: false,
    redirect_uri: config.redirect_uri || defaultRedirectUri.value })
}

function applyConnection(status) {
  connection.value = status
  tunnel.value = {
    enabled: Boolean(status?.development_tunnel_enabled),
    state: status?.development_tunnel_state || 'disabled',
    running: Boolean(status?.development_tunnel_active),
    callback_url: status?.development_callback_url || '',
    configured_origin: status?.development_configured_origin || '',
    message: status?.development_tunnel_message || ''
  }
  scheduleTunnelPoll()
}

function scheduleTunnelPoll() {
  clearTimeout(pollTimer)
  if (!disposed && tunnel.value.enabled && ['starting', 'running'].includes(tunnel.value.state)) {
    pollTimer = setTimeout(refreshTunnel, tunnel.value.state === 'starting' ? 1000 : 5000)
  }
}

async function refreshTunnel() {
  const request = tunnelRequest
  try {
    const status = await wpsCloudApi.getDevelopmentTunnel()
    if (disposed || request !== tunnelRequest) return
    tunnel.value = { ...status, configured_origin: status.configured_public_url || '' }
    if (connection.value) Object.assign(connection.value, {
      development_tunnel_active: status.running,
      development_callback_origin: status.public_url,
      development_callback_url: status.callback_url
    })
    scheduleTunnelPoll()
  } catch {
    if (disposed || request !== tunnelRequest) return
    tunnel.value = { ...tunnel.value, state: 'error', running: false, callback_url: '', message: '无法确认联调状态，请重新读取。' }
    if (connection.value) connection.value.development_tunnel_active = false
  }
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const [config, status] = await Promise.all([
      userStore.isAdmin ? wpsCloudApi.getConfig() : null, wpsCloudApi.getConnection()
    ])
    applyConnection(status)
    if (config) applyConfig(config)
  } catch {
    loadError.value = '读取 WPS 配置失败，请重新读取。'
  } finally {
    loading.value = false
  }
}

async function saveConfig() {
  if (draft.enabled && (!draft.app_id.trim() ||
      (!draft.app_secret && (!savedConfig.value.secret_configured || draft.clear_secret)))) {
    message.error('启用 WPS 前请填写应用 ID、密钥和回调地址；清除密钥时请先停用 WPS。')
    return
  }
  const callback = wpsCallbackUrl(draft.redirect_uri.trim())
  if (draft.redirect_uri && !callback) {
    message.error('请输入公网 HTTPS 回调地址，路径为 /auth/wps/callback；WPS 正式授权不能使用 localhost。')
    return
  }
  if (draft.enabled && !callback && !tunnel.value.enabled) {
    message.error('请填写公网 HTTPS 回调地址，或在开发环境启动开发联调后再启用 WPS。')
    return
  }
  saving.value = true
  try {
    applyConfig(await wpsCloudApi.saveConfig({
      enabled: draft.enabled, app_id: draft.app_id.trim(), app_secret: draft.app_secret.trim(),
      clear_secret: draft.clear_secret, redirect_uri: callback
    }))
    applyConnection(await wpsCloudApi.getConnection())
    message.success('WPS 配置已保存')
  } catch {
    message.error('保存失败，请检查配置并重试。')
  } finally {
    saving.value = false
  }
}

async function startDevelopmentTunnel() {
  startingTunnel.value = true
  tunnelRequest++
  clearTimeout(pollTimer)
  try {
    tunnel.value = await wpsCloudApi.startDevelopmentTunnel()
    await refreshTunnel()
    scheduleTunnelPoll()
  } catch {
    tunnel.value = { ...tunnel.value, state: 'error', running: false, callback_url: '', message: '无法启动联调，请重新读取状态后重试。' }
  } finally {
    startingTunnel.value = false
  }
}

async function stopDevelopmentTunnel() {
  stoppingTunnel.value = true
  tunnelRequest++
  clearTimeout(pollTimer)
  try {
    tunnel.value = await wpsCloudApi.stopDevelopmentTunnel()
    applyConnection(await wpsCloudApi.getConnection())
    message.success('开发联调已停止，旧联调授权需要重新发起')
  } catch {
    message.error('开发联调停止失败，请重试。')
  } finally {
    stoppingTunnel.value = false
  }
}

async function copyDevelopmentCallback() {
  try {
    await navigator.clipboard.writeText(tunnel.value.callback_url)
    message.success('已复制开发联调回调地址，请在 WPS 后台登记')
  } catch {
    message.error('复制失败，请手动复制开发联调地址。')
  }
}

function openDevelopmentCallback() {
  if (tunnel.value.public_url) window.open(`${tunnel.value.public_url}/workspace?tab=cloud`, '_blank', 'noopener,noreferrer')
}

async function copyCallback() {
  try {
    await navigator.clipboard.writeText(wpsCallbackUrl(draft.redirect_uri.trim()))
    message.success('已复制完整回调地址，请在 WPS 后台登记相同地址')
  } catch {
    message.error('复制失败，请手动复制回调地址。')
  }
}

async function connect() {
  if (!authorizationOrigin.value || originMismatch.value) return
  connecting.value = true
  try {
    const data = await wpsCloudApi.authorize()
    window.location.assign(data.authorization_url)
  } catch {
    message.error('无法开始 WPS 授权，请重新检查配置。')
    connecting.value = false
  }
}

async function checkConnection() {
  checking.value = true
  try {
    applyConnection(await wpsCloudApi.checkConnection())
    if (connection.value.connected) message.success('WPS 连接正常')
  } catch {
    message.error('检查失败，请稍后重试。')
  } finally {
    checking.value = false
  }
}

async function disconnect() {
  disconnecting.value = true
  try {
    applyConnection(await wpsCloudApi.disconnect())
    message.success('已断开 WPS 连接')
  } catch {
    message.error('断开失败，请重试。')
  } finally {
    disconnecting.value = false
  }
}

onMounted(load)
onUnmounted(() => { disposed = true; tunnelRequest++; clearTimeout(pollTimer) })
</script>

<style scoped lang="less">
.section-title { margin: 0 0 12px; color: var(--gray-900); font-size: 16px; font-weight: 600; }
.wps-settings-panel { padding: 20px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-0); }
.section-description, .field-help { color: var(--color-text-secondary); font-size: 12px; line-height: 1.6; }
.section-description { margin: 0 0 20px; }
.field-help { margin: 6px 0 0; }
.wps-form { margin-top: 16px; }
.enable-row, .config-actions, .connection-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.unsaved { color: var(--color-warning-900); font-size: 12px; }
.callback-field { margin-top: 12px; }
.callback-input { display: flex; align-items: center; gap: 8px; }
.callback-notice { margin-top: 12px; }
.development-tunnel { margin: 4px 0 20px; padding: 16px; border: 1px solid var(--gray-150); border-radius: 8px; background: var(--gray-25); }
.development-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.development-heading h4 { margin: 0; color: var(--gray-900); font-size: 14px; font-weight: 600; }
.tunnel-url { margin-top: 12px; }
.tunnel-actions { margin-top: 12px; }
.connection-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--gray-150); }
.connection-info { min-width: 0; }
.connection-info h4 { margin: 0 0 6px; color: var(--gray-900); font-size: 14px; font-weight: 500; }
.connection-info p { margin: 0; color: var(--color-text-secondary); font-size: 12px; overflow-wrap: anywhere; }
.connection-info .connected { display: flex; align-items: center; gap: 6px; color: var(--color-success-700); }
@media (max-width: 680px) {
  .wps-settings-panel { padding: 16px; }
  .connection-row { align-items: flex-start; flex-direction: column; }
}
</style>
