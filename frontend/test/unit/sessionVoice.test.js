import assert from 'node:assert/strict'
import test from 'node:test'
import { nextTick } from 'vue'
import { mountSessionWorkspace } from '../helpers/sessionWorkspace.js'

const local = { voiceURI: 'local', lang: 'zh-CN', localService: true }
const remote = { voiceURI: 'remote', lang: 'zh-CN', localService: false }

/** 只替换浏览器 API，验证真实会话的草稿和播放结果。 */
function browserSpeech() {
  const recognitions = []
  const utterances = []
  let availableVoices = [remote, local]
  let playing = null
  const window = {
    isSecureContext: true,
    webkitSpeechRecognition: class {
      start() {
        recognitions.push(this)
      }
      stop() {
        this.onend()
      }
      abort() {
        this.aborted = true
      }
    },
    SpeechSynthesisUtterance: class {
      constructor(text) {
        this.text = text
      }
    },
    speechSynthesis: {
      addEventListener() {},
      removeEventListener() {},
      getVoices: () => availableVoices,
      speak: (utterance) => {
        playing = utterance
        utterances.push(utterance)
      },
      cancel: () => {
        playing = null
      }
    }
  }
  return {
    window, recognitions, utterances, playing: () => playing,
    setVoices: (voices) => { availableVoices = voices }
  }
}

function login(state, uid = 'alice') {
  state.userStore.token = 'fixture'
  state.userStore.uid = uid
}

const result = (text) => ({ results: [Object.assign([{ transcript: text }], { isFinal: true })] })

test('听写在现有草稿尾部追加，未提交消息，切换会话后丢弃旧回调', async (t) => {
  const browser = browserSpeech()
  const { state } = await mountSessionWorkspace(t, undefined, { window: browser.window, stubChildren: true })
  login(state)
  state.agentDetails.value.fixture = { name: 'Fixture', can_run: true }
  state.selectedAgentId.value = 'fixture'
  state.voice.savePreferences({ dictationEnabled: true, recognitionLanguage: 'en-US' })
  let submissions = 0
  t.mock.method(state.sessionInput, 'send', () => { submissions++ })
  state.userInput.value = '已有文字'

  state.toggleDictation()
  assert.equal(browser.recognitions.length, 1)
  const recognition = browser.recognitions[0]
  assert.equal(recognition.lang, 'en-US')
  recognition.onresult(result('New text'))
  state.toggleDictation()
  assert.equal(state.userInput.value, '已有文字\n\nNew text')
  assert.equal(submissions, 0)

  state.toggleDictation()
  const previous = browser.recognitions[1]
  assert.ok(previous)
  state.currentThreadId.value = 'new-thread'
  assert.equal(previous.aborted, true)
  state.userInput.value = '其他会话'
  previous.onresult(result('迟到的结果'))
  previous.onend()
  assert.equal(state.userInput.value, '其他会话')
  assert.equal(submissions, 0)
})

test('权限拒绝立即结束识别，切换账户后各自恢复语音偏好', async (t) => {
  const browser = browserSpeech()
  const { state } = await mountSessionWorkspace(t, undefined, { window: browser.window, stubChildren: true })
  const values = new Map()
  t.mock.method(globalThis.localStorage, 'getItem', (key) => values.get(key) || null)
  t.mock.method(globalThis.localStorage, 'setItem', (key, value) => values.set(key, value))
  login(state)
  state.voice.savePreferences({ dictationEnabled: true, rate: 1.4, recognitionLanguage: 'en-US' })
  let error
  const owner = Symbol()
  state.voice.startDictation(owner, {
    onText: () => assert.fail('拒绝权限后不应转写'),
    onError: (value) => { error = value }
  })
  browser.recognitions[0].onerror({ error: 'not-allowed' })
  assert.match(error, /权限已被拒绝/)
  assert.equal(state.voice.dictationOwner, null)
  state.userStore.uid = 'bob'
  assert.equal(state.voice.preferences.rate, 1)
  assert.equal(state.voice.preferences.dictationEnabled, false)
  state.voice.savePreferences({ rate: 0.7 })
  state.userStore.uid = 'alice'
  assert.equal(state.voice.preferences.rate, 1.4)
  assert.equal(state.voice.preferences.recognitionLanguage, 'en-US')
  assert.equal(state.voice.preferences.dictationEnabled, true)
})

test('自动朗读只接受可见会话的新完成结果，用本地音色并且每条只读一次', async (t) => {
  const browser = browserSpeech()
  const { state, props } = await mountSessionWorkspace(t, undefined, {
    threadId: 'thread', window: browser.window, stubChildren: true
  })
  login(state)
  t.mock.method(state.chatThreadsStore, 'markThreadViewed', async () => {})
  const complete = (id, overrides = {}) => state.runtimeView.onTerminalDetected({
    threadId: 'thread', runId: 'run', observedAt: performance.now(),
    finalAnswer: { id, completedAt: Date.now(), content: '**正式回复**\n\n[说明](https://example.org)\n\n```widget\n{"secret":true}\n```' },
    ...overrides
  })
  complete('disabled')
  assert.equal(browser.utterances.length, 0)

  state.voice.savePreferences({ autoRead: true, voiceURI: 'remote', rate: 1.4 })
  complete('history', { finalAnswer: undefined })
  complete('other-thread', { threadId: 'other' })
  complete('prior-view', { observedAt: 0 })
  assert.equal(browser.utterances.length, 0)

  complete('result')
  assert.equal(browser.playing()?.text, '正式回复\n\n说明')
  assert.equal(browser.playing()?.voice, local)
  assert.equal(browser.playing()?.rate, 1.4)
  browser.playing().onend()
  complete('result')
  assert.equal(browser.utterances.length, 1)

  complete('new')
  assert.equal(browser.utterances.length, 2)
  props.visible = false
  assert.equal(browser.playing(), null)
  complete('hidden')
  props.visible = true
  await nextTick()
  complete('delayed-event', { finalAnswer: { id: 'delayed-event', completedAt: Date.now() - 1000, content: '旧回复' } })
  assert.equal(browser.utterances.length, 2)
  browser.setVoices([remote])
  complete('remote-only')
  assert.equal(browser.utterances.length, 2)
})
