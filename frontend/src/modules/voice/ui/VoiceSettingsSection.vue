<template>
  <div class="settings-page">
    <div class="settings-header">
      <h2 class="settings-section-title">语音</h2>
    </div>

    <section class="settings-section-block" aria-labelledby="voice-dictation-heading">
      <h3 id="voice-dictation-heading" class="voice-heading">语音识别</h3>
      <div class="settings-form-panel">
        <div class="settings-form-row">
          <div class="settings-form-copy">
            <span class="settings-form-label">启用听写</span>
            <p class="settings-form-description">
              听写使用默认麦克风，音频可能由浏览器供应商远程处理。
            </p>
            <p v-if="!voice.canRecognize" class="settings-form-description" role="status">
              当前浏览器无法使用听写。请使用支持语音识别的 Chromium 浏览器和 HTTPS 或 localhost。
            </p>
          </div>
          <a-switch
            aria-label="启用听写"
            :checked="voice.preferences.dictationEnabled"
            :disabled="!voice.canRecognize"
            @change="setPreference('dictationEnabled', $event)"
          />
        </div>
        <div class="settings-form-row">
          <span class="settings-form-label settings-form-copy">识别语言</span>
          <div class="settings-form-control is-compact">
            <a-select
              aria-label="识别语言"
              :value="voice.preferences.recognitionLanguage"
              :options="languages"
              @change="setPreference('recognitionLanguage', $event)"
            />
          </div>
        </div>
      </div>
    </section>

    <section class="settings-section-block" aria-labelledby="voice-playback-heading">
      <h3 id="voice-playback-heading" class="voice-heading">文字朗读</h3>
      <div class="settings-form-panel">
        <div class="settings-form-row">
          <div class="settings-form-copy">
            <span class="settings-form-label">音色</span>
            <p v-if="!voice.canSpeak" class="settings-form-description" role="status">当前浏览器不支持文字朗读。</p>
            <p v-else-if="!voice.voices.length" class="settings-form-description" role="status">暂未获取到可选音色。</p>
          </div>
          <div class="settings-form-control is-compact">
            <a-select
              aria-label="音色"
              :value="voice.preferences.voiceURI"
              :options="voiceOptions"
              :disabled="!voice.canSpeak"
              @change="setPreference('voiceURI', $event)"
            />
          </div>
        </div>
        <div class="settings-form-row">
          <span class="settings-form-label settings-form-copy">语速</span>
          <div class="voice-actions">
            <a-input-number
              aria-label="语速"
              :value="voice.preferences.rate"
              :min="0.5"
              :max="2"
              :step="0.1"
              :disabled="!voice.canSpeak"
              @change="setRate"
            />
            <a-button :disabled="!voice.canSpeak || Boolean(voice.dictationOwner)" @click="togglePreview">
              <template #icon>
                <Square v-if="previewing" :size="14" aria-hidden="true" />
                <Volume2 v-else :size="14" aria-hidden="true" />
              </template>
              {{ previewing ? '停止试听' : '试听' }}
            </a-button>
          </div>
        </div>
        <div class="settings-form-row">
          <div class="settings-form-copy">
            <span class="settings-form-label">自动朗读回复</span>
            <p class="settings-form-description">
              仅使用本地音色。当前选用非本地音色时，将使用识别语言对应的本地音色。
            </p>
            <p v-if="voice.canSpeak && !voice.automaticVoice" class="settings-form-description" role="status">
              当前没有可用于自动朗读的本地音色。
            </p>
          </div>
          <a-switch
            aria-label="自动朗读回复"
            :checked="voice.preferences.autoRead"
            :disabled="!voice.canSpeak"
            @change="setPreference('autoRead', $event)"
          />
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onUnmounted, watch } from 'vue'
import { message } from 'ant-design-vue'
import { Square, Volume2 } from '@lucide/vue'
import { useVoiceStore } from '../model/voice.js'

const props = defineProps({ active: { type: Boolean, default: true } })
const voice = useVoiceStore()
const previewOwner = Symbol('voice-preview')
const previewing = computed(() => voice.playback?.owner === previewOwner)
const languages = [
  { value: 'zh-CN', label: '简体中文（普通话）' },
  { value: 'zh-TW', label: '繁體中文（國語）' },
  { value: 'yue-HK', label: '粵語' },
  { value: 'en-US', label: '英语（美国）' },
  { value: 'en-GB', label: '英语（英国）' },
  { value: 'ja-JP', label: '日语' },
  { value: 'ko-KR', label: '韩语' },
  { value: 'fr-FR', label: '法语' },
  { value: 'de-DE', label: '德语' },
  { value: 'es-ES', label: '西班牙语' }
]
const samples = {
  zh: '你好，这是一段语音试听。',
  yue: '你好，呢段係語音試聽。',
  en: 'Hello, this is a voice preview.',
  ja: 'こんにちは。音声のプレビューです。',
  ko: '안녕하세요. 음성 미리 듣기입니다.',
  fr: 'Bonjour, voici un aperçu de la voix.',
  de: 'Hallo, dies ist eine Stimmprobe.',
  es: 'Hola, esta es una muestra de voz.'
}
const voiceOptions = computed(() => [
  { value: '', label: '系统默认' },
  ...voice.voices.map((item) => ({
    value: item.voiceURI,
    label: `${item.name} · ${item.lang}${item.localService ? ' · 本地' : ''}`
  })),
  ...(voice.preferences.voiceURI && !voice.voices.some((item) => item.voiceURI === voice.preferences.voiceURI)
    ? [{ value: voice.preferences.voiceURI, label: '已选音色不可用', disabled: true }] : [])
])
const setPreference = (key, value) => voice.savePreferences({ [key]: value })
const setRate = (value) => {
  if (typeof value === 'number') setPreference('rate', value)
}
const stopPreview = () => voice.stopPlayback(previewOwner)
const togglePreview = () => {
  if (previewing.value) return stopPreview()
  const language = voice.selectedVoice?.lang || voice.preferences.recognitionLanguage
  voice.speak(previewOwner, 'preview', samples[language.split('-')[0]] || samples.en,
    { onError: (error) => message.warning(error) })
}

watch(() => props.active, (active) => {
  if (!active) stopPreview()
}, { flush: 'sync' })
onUnmounted(stopPreview)
</script>

<style scoped>
.voice-heading {
  margin: 0 0 8px;
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 600;
}

.voice-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.voice-actions .ant-input-number {
  width: 84px;
}

.voice-actions .ant-btn {
  display: inline-flex;
  align-items: center;
}
</style>
