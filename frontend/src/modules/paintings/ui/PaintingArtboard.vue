<script setup>
import { Download, RotateCcwSquare, RotateCwSquare, ZoomIn, ZoomOut, Maximize2 } from '@lucide/vue'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  images: { type: Array, required: true },
  pending: { type: Boolean, default: false },
  error: { type: String, default: '' }
})
const index = ref(0)
const zoom = ref(1)
const rotation = ref(0)
const offset = ref({ x: 0, y: 0 })
let drag = null
const active = computed(() => props.images[index.value])
const transformed = computed(() => ({
  transform: `translate(${offset.value.x}px, ${offset.value.y}px) rotate(${rotation.value}deg) scale(${zoom.value})`
}))

function resetView() {
  zoom.value = 1
  rotation.value = 0
  offset.value = { x: 0, y: 0 }
}

watch(() => props.images, () => {
  index.value = 0
  resetView()
})
watch(index, resetView)

function changeZoom(direction) {
  zoom.value = Math.max(0.25, Math.min(6, zoom.value * (direction > 0 ? 1.2 : 1 / 1.2)))
}

function beginPan(event) {
  if (event.button !== 0) return
  event.currentTarget.setPointerCapture(event.pointerId)
  drag = { id: event.pointerId, x: event.clientX - offset.value.x, y: event.clientY - offset.value.y }
}

function movePan(event) {
  if (drag?.id !== event.pointerId) return
  offset.value = { x: event.clientX - drag.x, y: event.clientY - drag.y }
}
</script>

<template>
  <section class="artboard" aria-label="画板" :aria-busy="pending">
    <div
      v-if="active"
      class="artboard-picture"
      @wheel.prevent="changeZoom(-$event.deltaY)"
      @pointerdown="beginPan"
      @pointermove="movePan"
      @lostpointercapture="drag = null"
      @dblclick="resetView"
    >
      <img :src="active.url" :style="transformed" alt="生成的图片" draggable="false" />
    </div>
    <div v-else-if="pending" class="painting-skeleton" role="status" aria-label="正在生成图片">
      <span>正在生成图片…</span>
    </div>
    <div v-if="active" class="artboard-tools" role="toolbar" aria-label="画板操作">
      <a
        class="artboard-tool"
        :href="active.url"
        :download="active.filename"
        aria-label="下载图片"
        title="下载图片"
      ><Download :size="17" /></a>
      <button type="button" class="artboard-tool" title="缩小" aria-label="缩小" @click="changeZoom(-1)">
        <ZoomOut :size="17" />
      </button>
      <button type="button" class="artboard-tool" title="放大" aria-label="放大" @click="changeZoom(1)">
        <ZoomIn :size="17" />
      </button>
      <button type="button" class="artboard-tool" title="向左旋转" aria-label="向左旋转" @click="rotation -= 90">
        <RotateCcwSquare :size="17" />
      </button>
      <button type="button" class="artboard-tool" title="向右旋转" aria-label="向右旋转" @click="rotation += 90">
        <RotateCwSquare :size="17" />
      </button>
      <button type="button" class="artboard-tool" title="适应窗口" aria-label="适应窗口" @click="resetView">
        <Maximize2 :size="17" />
      </button>
    </div>
    <div v-if="images.length > 1" class="artboard-images" role="group" aria-label="生成的图片">
      <button
        v-for="(image, imageIndex) in images" :key="image.url"
        type="button"
        :aria-pressed="index === imageIndex"
        :aria-label="`查看第 ${imageIndex + 1} 张图片`"
        @click="index = imageIndex"
      ><img :src="image.url" alt="" /></button>
    </div>
    <p v-if="error" class="artboard-error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped lang="less">
.artboard {
  position: relative;
  min-height: 0;
  min-width: 0;
  flex: 1;
  display: grid;
  place-items: center;
  overflow: hidden;
}

.artboard-picture {
  position: absolute;
  inset: 12px 54px;
  display: grid;
  place-items: center;
  cursor: grab;
  touch-action: none;

  &:active { cursor: grabbing; }
  img {
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    object-fit: contain;
    pointer-events: none;
  }
}

.artboard-tools {
  position: absolute;
  top: 50%;
  left: 12px;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  padding: 4px;
  border: 1px solid var(--gray-100);
  border-radius: 21px;
  background: var(--gray-0);
  box-shadow: 0 2px 10px #0002;
}

.artboard-tool {
  display: grid;
  place-items: center;
  width: 31px;
  height: 30px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--gray-700);
  cursor: pointer;

  &:hover { background: var(--gray-100); }
}

.artboard-images {
  position: absolute;
  bottom: 8px;
  display: flex;
  gap: 5px;

  button {
    width: 39px;
    height: 39px;
    padding: 2px;
    border: 1px solid var(--gray-100);
    border-radius: 5px;
    background: var(--gray-0);
    cursor: pointer;
  }

  button[aria-pressed='true'] { border-color: var(--gray-700); }
  img { width: 100%; height: 100%; object-fit: cover; border-radius: 3px; }
}

.painting-skeleton {
  display: grid;
  place-items: center;
  width: min(65%, 520px);
  height: min(65%, 420px);
  background: var(--gray-50);
  border-radius: 16px;
  color: var(--gray-700);
}

.artboard-error {
  position: absolute;
  bottom: 16px;
  max-width: calc(100% - 60px);
  margin: 0;
  padding: 7px 12px;
  border-radius: 6px;
  background: var(--gray-50);
  color: var(--color-error-700);
}
</style>
