<template>
  <div class="home-container">
    <div v-if="isLoading" class="loading-container" role="status">
      <a-spin size="large" />
      <p>正在连接服务…</p>
    </div>
    <div v-else-if="error" class="error-container">
      <a-result status="error" :title="error.title" :sub-title="error.message">
        <template #extra>
          <a-button type="primary" @click="loadData">重试</a-button>
        </template>
      </a-result>
    </div>
    <template v-else>
      <header class="site-header page-width">
        <div class="brand">
          <img :src="infoStore.branding.logo" alt="" width="32" height="32" />
          <span>{{ infoStore.branding.name }}</span>
        </div>
        <nav class="header-actions" aria-label="首页导航">
          <button
            type="button"
            class="theme-toggle lucide-icon-btn"
            :aria-label="`切换主题，当前${themeStore.themeLabel}`"
            :title="`主题：${themeStore.themeLabel}`"
            @click="themeStore.toggleTheme()"
          >
            <Sun v-if="themeStore.themeMode === 'light'" :size="17" aria-hidden="true" />
            <Moon v-else-if="themeStore.themeMode === 'dark'" :size="17" aria-hidden="true" />
            <Monitor v-else :size="17" aria-hidden="true" />
          </button>
          <button type="button" class="header-chat" @click="router.push('/agent')">
            去对话 <ArrowUpRight :size="16" aria-hidden="true" />
          </button>
        </nav>
      </header>
      <main class="home-main page-width" aria-labelledby="home-title">
        <div class="home-workspace">
          <div class="home-intro">
            <div class="hero-content">
              <span class="hero-marker" aria-hidden="true"></span>
              <h1 id="home-title">企业对话<br />工作台</h1>
              <p v-if="infoStore.branding.title">{{ infoStore.branding.title }}</p>
            </div>
            <div class="hero-illustration" aria-hidden="true">
              <span class="hero-node"><Files :size="22" /></span>
              <span class="hero-node hero-node--agent"><Bot :size="30" /></span>
              <span class="hero-node"><Workflow :size="22" /></span>
            </div>
          </div>
          <div class="home-entry">
            <h2><MessageSquarePlus :size="23" aria-hidden="true" /> 开始对话</h2>
            <p class="entry-description">输入问题或任务，内容会带入新对话草稿。</p>
            <form class="entry-shell entry-composer" @submit.prevent="goToChat">
              <textarea
                id="home-prompt"
                v-model="prompt"
                aria-label="问题或任务"
                rows="2"
                placeholder="输入问题或任务"
                @focus="inputFocused = true"
                @blur="inputFocused = false"
                @keydown="handlePromptKeydown"
              ></textarea>
              <div class="entry-toolbar">
                <HomeAgentPreview :paused="inputFocused || Boolean(prompt.trim())" />
                <div class="entry-actions">
                  <span class="entry-model">
                    DeepSeek V4.1 Flash<ChevronDown :size="15" aria-hidden="true" />
                  </span>
                  <button type="submit" class="primary-button lucide-icon-btn">
                    <ArrowRight :size="17" aria-hidden="true" />
                    <span>继续</span>
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      </main>
      <footer class="site-footer page-width">
        <p>{{ infoStore.footer?.copyright || 'All rights reserved' }}</p>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  ArrowUpRight,
  Bot,
  ChevronDown,
  Files,
  MessageSquarePlus,
  Monitor,
  Moon,
  Sun,
  Workflow
} from '@lucide/vue'
import { useInfoStore } from '@/modules/settings/model/info'
import { healthApi } from '@/apis/system_api'
import HomeAgentPreview from '@/modules/home/ui/HomeAgentPreview.vue'
import { entryDraftHandoff } from '@/modules/session/model/thread_draft'
import { useThemeStore } from '@/shared/model/theme'

const router = useRouter()
const themeStore = useThemeStore()
const infoStore = useInfoStore()
const isLoading = ref(true)
const error = ref(null)
const prompt = ref('')
const inputFocused = ref(false)
/** 读取服务与品牌状态，失败时保留重试入口。 */
async function loadData() {
  isLoading.value = true
  error.value = null
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') throw new Error('服务不可用')
    await infoStore.loadInfoConfig()
  } catch {
    error.value = { title: '服务连接失败', message: '后端服务无法响应，请检查服务是否正常运行' }
  } finally {
    isLoading.value = false
  }
}

/** 文本作为新会话草稿交接，身份校验与登录返回由路由守卫处理。 */
async function goToChat() {
  const text = prompt.value.trim()
  if (text) entryDraftHandoff.save(text)
  const failure = await router.push('/agent')
  if (!failure) prompt.value = ''
}

/** 中文候选词确认和 Shift+Enter 保留输入行为。 */
function handlePromptKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void goToChat()
}
onMounted(loadData)
</script>

<style lang="less" scoped>
.home-container {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  color: var(--gray-900);
  background: var(--gray-30);
}

.page-width {
  width: min(1360px, 100%);
  margin-inline: auto;
  padding-inline: clamp(24px, 5vw, 72px);
}

.loading-container,
.error-container {
  min-height: 100dvh;
  display: grid;
  place-content: center;
  padding: 32px;
}

.loading-container {
  justify-items: center;
  gap: 16px;
  color: var(--gray-600);
}

.site-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 80px;
  gap: 20px;
}

.brand,
.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand {
  min-width: 0;
  color: var(--gray-900);
  font-size: 18px;
  font-weight: 650;
  overflow-wrap: anywhere;
}

.brand img {
  object-fit: contain;
  flex-shrink: 0;
}

.header-actions {
  flex-shrink: 0;
}

.header-chat,
.theme-toggle,
.primary-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid var(--gray-300);
  border-radius: 8px;
  background: var(--gray-0);
  color: var(--gray-900);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 3px;
  }
}

.header-chat {
  min-height: 38px;
  padding-inline: 13px;
}

.theme-toggle {
  width: 38px;
  height: 38px;
}

.header-chat:hover,
.theme-toggle:hover {
  border-color: var(--main-color);
  color: var(--main-800);
}

.home-main {
  flex: 1;
  display: grid;
  align-items: center;
  padding-block: 24px 36px;
}

.home-workspace {
  display: grid;
  grid-template-columns: minmax(0, 5fr) minmax(0, 6fr);
  min-height: 490px;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 20px;
  background: var(--gray-0);
}

.home-intro {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 40px;
  padding: clamp(28px, 4vw, 60px);
  color: #f2fbfa;
  background: radial-gradient(ellipse at 90% 105%, #196553 0, transparent 65%), #0c3842;

  &::before {
    position: absolute;
    z-index: -1;
    inset: 0;
    background-image:
      linear-gradient(#8de4d022 1px, transparent 1px),
      linear-gradient(90deg, #8de4d022 1px, transparent 1px);
    background-size: 32px 32px;
    mask-image: linear-gradient(140deg, transparent 30%, #000);
    content: '';
    pointer-events: none;
  }
}

.hero-marker {
  display: block;
  width: 36px;
  height: 4px;
  margin-bottom: 24px;
  border-radius: 2px;
  background: #78e5ce;
}

.hero-content h1 {
  margin: 0;
  color: inherit;
  font-size: clamp(38px, 4.5vw, 68px);
  font-weight: 700;
  line-height: 1.1;
}

.hero-content p {
  max-width: 22em;
  margin: 20px 0 0;
  color: #c4dfdf;
  font-size: 15px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.hero-illustration {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: min(100%, 300px);

  &::before {
    position: absolute;
    z-index: -1;
    inset-inline: 16px;
    border-top: 1px dashed #78e5ce99;
    content: '';
  }
}

.hero-node {
  display: grid;
  width: 48px;
  min-height: 48px;
  place-items: center;
  border: 1px solid #78e5ce65;
  border-radius: 10px;
  background: #143f48;
  color: #a4eee0;
}

.hero-node--agent {
  width: 66px;
  min-height: 66px;
  border-color: #78e5ce;
  border-radius: 15px;
  background: #176358;
  color: #f2fbfa;
}

.home-entry {
  align-self: center;
  min-width: 0;
  padding: clamp(24px, 4vw, 64px);
}

.home-entry h2 {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: var(--gray-1000);
  font-size: 25px;
  font-weight: 650;

  svg {
    flex-shrink: 0;
    color: var(--main-800);
  }
}

.entry-description {
  margin: 8px 0 26px;
  color: var(--gray-600);
  font-size: 14px;
  line-height: 1.55;
}

.entry-shell {
  overflow: hidden;
  border: 1px solid var(--gray-300);
  border-radius: 12px;
  background: var(--gray-0);
  transition: border-color 180ms ease;

  &:focus-within {
    border-color: var(--main-color);
  }
}

#home-prompt {
  display: block;
  width: 100%;
  height: 118px;
  padding: 16px;
  border: 0;
  outline: none;
  resize: none;
  background: transparent;
  color: var(--gray-900);
  font: inherit;
  font-size: 16px;
  line-height: 24px;
  scrollbar-width: thin;

  &::placeholder {
    color: var(--gray-600);
  }
}

.entry-toolbar,
.entry-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.entry-toolbar {
  min-height: 56px;
  justify-content: space-between;
  padding: 8px 12px;
  border-top: 1px solid var(--gray-200);
  background: var(--gray-25);
}

.entry-toolbar :deep(.agent-preview) {
  min-width: 0;
  white-space: nowrap;
}

.entry-model {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--gray-600);
  font-size: 12px;
  white-space: nowrap;
}

.primary-button {
  min-height: 36px;
  padding: 0 10px;
  border-color: #0c3842;
  background: #0c3842;
  color: #f2fbfa;
  white-space: nowrap;

  &:hover,
  &:active {
    border-color: #195449;
    background: #195449;
  }
}

:global(:root.dark .home-container .primary-button) {
  border-color: #78e5ce;
  background: #78e5ce;
  color: #0c3842;
}

:global(:root.dark .home-container .primary-button:hover),
:global(:root.dark .home-container .primary-button:active) {
  border-color: #a4eee0;
  background: #a4eee0;
}

.site-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60px;
  color: var(--gray-600);
  font-size: 12px;
  text-align: center;

  p {
    margin: 0;
    overflow-wrap: anywhere;
  }
}

@media (max-width: 960px) {
  .home-workspace {
    grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
    min-height: 420px;
  }

  .entry-toolbar {
    flex-wrap: wrap;
  }

  .entry-actions {
    margin-inline-start: auto;
  }
}

@media (max-width: 700px) {
  .home-workspace {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (prefers-reduced-motion: reduce) {
  .entry-shell {
    transition: none;
  }
}
</style>
