<template>
  <span
    class="fallback-avatar"
    :class="[`fallback-avatar--${shape}`, { 'fallback-avatar--icon': isFallback }]"
    :style="avatarStyle"
    :data-avatar-kind="kind"
    :data-avatar-state="isFallback ? 'fallback' : 'image'"
    :data-avatar-seed="seed"
    :aria-hidden="decorative ? 'true' : undefined"
    :role="isFallback && !decorative ? 'img' : undefined"
    :aria-label="isFallback && !decorative ? resolvedAlt : undefined"
  >
    <component
      :is="fallbackIcon"
      v-if="isFallback"
      class="fallback-avatar-icon"
      aria-hidden="true"
    />
    <img
      v-else
      :key="`${attempt}:${source.url}`"
      ref="imageElement"
      class="fallback-avatar-image"
      :src="source.url"
      :alt="decorative ? '' : resolvedAlt"
      @error="handleImageError"
    />
  </span>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Bot, User, Workflow } from '@lucide/vue'
import { useThemeStore } from '@/shared/model/theme'
import { defaultStyles, generate, imageUrl } from '@/shared/lib/avatar/generator'

const props = defineProps({
  src: { type: String, default: '' },
  name: { type: String, default: '' },
  seed: { type: [String, Number], default: '' },
  kind: {
    type: String,
    default: 'user',
    validator: (value) => ['user', 'agent', 'session'].includes(value)
  },
  style: { type: String, default: '' },
  preset: { type: String, default: '' },
  dark: { type: String, default: 'auto' },
  size: { type: [String, Number], default: 32 },
  shape: {
    type: String,
    default: 'circle',
    validator: (value) => ['circle', 'square', 'rounded'].includes(value)
  },
  alt: { type: String, default: '' },
  decorative: { type: Boolean, default: false }
})
const emit = defineEmits(['fallback'])
const themeStore = useThemeStore()
const imageElement = ref(null)
const failed = ref(false)
const attempt = ref(0)
const source = computed(() => {
  const src = String(props.src || '').trim()
  if (src) return { url: src }
  try {
    return {
      url: imageUrl(
        generate(
          {
            style: props.style || defaultStyles[props.kind],
            preset: props.preset || (props.kind === 'agent' ? 'soft' : 'default'),
            dark: props.dark,
            shape: props.shape,
            seed: `${props.kind}:${String(props.seed ?? '').trim() || String(props.name || '').trim() || 'default'}`
          },
          themeStore.isDark
        )
      )
    }
  } catch (error) {
    return { error }
  }
})
const isFallback = computed(() => failed.value || !!source.value.error)
const fallbackIcon = computed(() => ({ agent: Bot, user: User, session: Workflow })[props.kind])
const resolvedAlt = computed(
  () =>
    props.alt ||
    props.name ||
    { agent: '智能体头像', user: '用户头像', session: '会话图标' }[props.kind]
)
const avatarStyle = computed(() => ({
  '--fallback-avatar-size': typeof props.size === 'number' ? `${props.size}px` : props.size,
  '--fallback-avatar-background': `var(--avatar-${props.kind === 'user' ? 'user' : 'agent'}-background)`,
  '--fallback-avatar-ink': `var(--avatar-${props.kind === 'user' ? 'user' : 'agent'}-ink)`
}))

/** 仅处理当前图片的失败，迟到的旧请求不覆盖新来源。 */
function handleImageError(event) {
  if (event.target !== imageElement.value) return
  failed.value = true
  emit('fallback', { reason: 'image', src: source.value.url })
}
/** 网络恢复后只重试失败图片。 */
function retryImage() {
  if (!failed.value) return
  failed.value = false
  attempt.value++
}
watch(
  source,
  (value) => {
    failed.value = false
    if (value.error) emit('fallback', { reason: 'generation', error: value.error })
  },
  { immediate: true, flush: 'sync' }
)
onMounted(() => window.addEventListener('online', retryImage))
onUnmounted(() => window.removeEventListener('online', retryImage))
</script>

<style lang="less" scoped>
.fallback-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--fallback-avatar-size);
  height: var(--fallback-avatar-size);
  flex: 0 0 var(--fallback-avatar-size);
  overflow: hidden;
  line-height: 1;
  user-select: none;
}
.fallback-avatar--circle {
  border-radius: 50%;
}
.fallback-avatar--rounded {
  border-radius: 7px;
}
.fallback-avatar--icon {
  background: var(--fallback-avatar-background);
  color: var(--fallback-avatar-ink);
}
.fallback-avatar-image {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.fallback-avatar-icon {
  width: 55%;
  height: 55%;
  stroke-width: 1.7;
}
</style>
