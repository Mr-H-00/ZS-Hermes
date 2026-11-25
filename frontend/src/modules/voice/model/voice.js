import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { defineStore } from 'pinia'
import { useUserStore } from '@/modules/identity'
import { toSpokenText } from './readableAnswer.js'

export const voicePlaybackScope = Symbol('voicePlaybackScope')

const defaults = {
  dictationEnabled: false,
  recognitionLanguage: 'zh-CN',
  voiceURI: '',
  rate: 1,
  autoRead: false
}

function normalizePreferences(value = {}) {
  return {
    dictationEnabled: value?.dictationEnabled === true,
    recognitionLanguage: /^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$/i.test(value?.recognitionLanguage || '')
      ? value.recognitionLanguage : defaults.recognitionLanguage,
    voiceURI: typeof value?.voiceURI === 'string' ? value.voiceURI : '',
    rate: Number.isFinite(value?.rate) && value.rate >= 0.5 && value.rate <= 2 ? value.rate : 1,
    autoRead: value?.autoRead === true
  }
}

function dictationError(code) {
  if (code === 'not-allowed' || code === 'service-not-allowed') return '麦克风或语音识别权限已被拒绝'
  if (code === 'audio-capture') return '无法使用默认麦克风'
  if (code === 'no-speech') return '未识别到语音，请重试'
  if (code === 'network') return '浏览器语音识别服务连接失败'
  return '语音识别失败，请重试'
}

/** 同一页面只持有一次麦克风或播放操作，事件结果只返回该操作的发起方。 */
export const useVoiceStore = defineStore('voice', () => {
  const user = useUserStore()
  const uid = computed(() => user.isLoggedIn ? user.uid : '')
  const storageKey = computed(() => `yuxi:voice:${uid.value}`)
  const preferences = ref({ ...defaults })
  const browser = globalThis.window
  const synthesis = browser?.speechSynthesis
  const Utterance = browser?.SpeechSynthesisUtterance
  const Recognition = browser?.SpeechRecognition || browser?.webkitSpeechRecognition
  const canRecognize = Boolean(browser?.isSecureContext && Recognition)
  const canSpeak = Boolean(synthesis && Utterance)
  const voices = shallowRef([])
  const playback = shallowRef(null)
  const dictationOwner = shallowRef(null)
  const dictationStopping = ref(false)
  const automaticallyRead = new Set()
  let recognizer

  const refreshVoices = () => {
    voices.value = synthesis?.getVoices() || []
  }
  const selectedVoice = computed(() =>
    voices.value.find((voice) => voice.voiceURI === preferences.value.voiceURI)
  )
  const automaticVoice = computed(() => {
    if (selectedVoice.value?.localService === true) return selectedVoice.value
    const language = preferences.value.recognitionLanguage.toLowerCase().split('-')[0]
    return voices.value.find((voice) =>
      voice.localService === true && voice.lang?.toLowerCase().split('-')[0] === language)
  })
  synthesis?.addEventListener('voiceschanged', refreshVoices)
  refreshVoices()

  /** 本地偏好按已确认的登录 UID 隔离。 */
  function savePreferences(changes) {
    if (!uid.value) return
    preferences.value = normalizePreferences({ ...preferences.value, ...changes })
    if (!preferences.value.dictationEnabled) cancelDictation(dictationOwner.value)
    if (!preferences.value.autoRead && playback.value?.automatic) stopPlayback(playback.value.owner)
    try {
      localStorage.setItem(storageKey.value, JSON.stringify(preferences.value))
    } catch {
      // 禁用存储时保留当前页面内的设置。
    }
  }

  function stopPlayback(owner) {
    if (!playback.value || playback.value.owner !== owner) return
    playback.value = null
    synthesis.cancel()
  }

  /** 自动播放只接受明确由客户端本地语音服务提供的音色。 */
  function speak(owner, id, content, { automatic = false, onError } = {}) {
    if (!uid.value || !canSpeak || dictationOwner.value) return false
    if (automatic && (!preferences.value.autoRead || playback.value || automaticallyRead.has(id))) return false
    const text = toSpokenText(content)
    if (!text) return false
    refreshVoices()
    const { recognitionLanguage, rate } = preferences.value
    const voice = automatic ? automaticVoice.value : selectedVoice.value
    if (automatic && !voice) return false
    if (playback.value) stopPlayback(playback.value.owner)
    const utterance = new Utterance(text)
    if (voice) utterance.voice = voice
    utterance.lang = voice?.lang || recognitionLanguage
    utterance.rate = rate
    const operation = { owner, id, automatic }
    if (automatic) automaticallyRead.add(id)
    playback.value = operation
    utterance.onend = () => {
      if (playback.value === operation) playback.value = null
    }
    utterance.onerror = ({ error }) => {
      if (playback.value !== operation) return
      playback.value = null
      if (error !== 'canceled' && error !== 'interrupted') onError?.('朗读失败，请检查浏览器的语音服务')
    }
    try {
      synthesis.speak(utterance)
      return true
    } catch {
      playback.value = null
      onError?.('朗读失败，请检查浏览器的语音服务')
      return false
    }
  }

  function clearDictation() {
    recognizer = null
    dictationOwner.value = null
    dictationStopping.value = false
  }

  /** 单次听写只提交最终文本，放弃后忽略浏览器的延迟事件。 */
  function startDictation(owner, { onText, onError }) {
    if (!uid.value || !canRecognize || !preferences.value.dictationEnabled || recognizer) return false
    if (playback.value) stopPlayback(playback.value.owner)
    const recognition = new Recognition()
    recognizer = recognition
    dictationOwner.value = owner
    recognition.lang = preferences.value.recognitionLanguage
    recognition.continuous = false
    recognition.interimResults = false
    let transcript = ''
    recognition.onresult = ({ results }) => {
      if (recognizer !== recognition) return
      transcript = Array.from(results).filter((result) => result.isFinal)
        .map((result) => result[0]?.transcript || '').join(' ').trim()
    }
    recognition.onerror = ({ error }) => {
      if (recognizer !== recognition) return
      clearDictation()
      onError(dictationError(error))
    }
    recognition.onend = () => {
      if (recognizer !== recognition) return
      clearDictation()
      if (transcript) onText(transcript)
      else onError(dictationError('no-speech'))
    }
    try {
      recognition.start()
      return true
    } catch (error) {
      clearDictation()
      onError(dictationError(error?.name === 'NotAllowedError' ? 'not-allowed' : ''))
      return false
    }
  }

  function stopDictation(owner) {
    if (dictationOwner.value !== owner || !recognizer || dictationStopping.value) return
    dictationStopping.value = true
    recognizer.stop()
  }

  function cancelDictation(owner) {
    if (dictationOwner.value !== owner || !recognizer) return
    const recognition = recognizer
    clearDictation()
    recognition.abort()
  }

  function release(owner) {
    stopPlayback(owner)
    cancelDictation(owner)
  }

  watch(uid, (id) => {
    stopPlayback(playback.value?.owner)
    cancelDictation(dictationOwner.value)
    automaticallyRead.clear()
    preferences.value = { ...defaults }
    if (!id) return
    try {
      preferences.value = normalizePreferences(JSON.parse(localStorage.getItem(storageKey.value)))
    } catch {
      // 旧值不可读时使用默认值。
    }
  }, { flush: 'sync', immediate: true })

  onScopeDispose(() => {
    stopPlayback(playback.value?.owner)
    cancelDictation(dictationOwner.value)
    synthesis?.removeEventListener('voiceschanged', refreshVoices)
  })

  return {
    preferences, savePreferences, voices, selectedVoice, automaticVoice,
    canRecognize, canSpeak, playback, speak, stopPlayback,
    dictationOwner, dictationStopping, startDictation, stopDictation, cancelDictation, release
  }
})
