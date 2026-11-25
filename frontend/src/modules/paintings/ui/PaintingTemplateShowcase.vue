<script setup>
import { computed } from 'vue'

const props = defineProps({
  templates: { type: Array, required: true },
  selected: { type: String, default: '' }
})
const emit = defineEmits(['select'])
const positions = [
  { offset: -36.5, shift: 8, rotation: -7, scale: 0.82, depth: 1, opacity: 0.70 },
  { offset: -18.25, shift: 3, rotation: -3, scale: 0.92, depth: 2, opacity: 0.90 },
  { offset: 0, shift: -2, rotation: 0, scale: 1.12, depth: 4, opacity: 1 },
  { offset: 18.25, shift: 3, rotation: 3, scale: 0.92, depth: 2, opacity: 0.90 },
  { offset: 36.5, shift: 8, rotation: 7, scale: 0.82, depth: 1, opacity: 0.70 }
]
const activeIndex = computed(() => {
  const index = props.templates.findIndex((template) => template.id === props.selected)
  return index >= 0 ? index : 2
})
const cards = computed(() => {
  const count = props.templates.length
  return props.templates.map((template, index) => {
    const midpoint = Math.floor(count / 2)
    const relative = ((index - activeIndex.value + midpoint + count) % count) - midpoint
    const position = positions[Math.max(-2, Math.min(2, relative)) + 2]
    return {
      ...template,
      hidden: Math.abs(relative) > 2,
      style: {
        '--offset': `${position.offset}cqw`,
        '--shift': `${position.shift}px`,
        '--rotation': `${position.rotation}deg`,
        '--scale': position.scale,
        zIndex: position.depth,
        '--card-opacity': position.opacity
      }
    }
  })
})

function move(offset) {
  const count = props.templates.length
  if (count) emit('select', props.templates[(activeIndex.value + offset + count) % count])
}
</script>

<template>
  <div
    class="template-carousel"
    role="group"
    aria-label="提示词模板"
    @keydown.left.prevent="move(-1)"
    @keydown.right.prevent="move(1)"
  >
    <button
      v-for="card in cards"
      :key="card.id"
      type="button"
      class="template-card"
      :class="{ 'is-hidden': card.hidden }"
      :aria-hidden="card.hidden || undefined"
      :aria-label="card.label"
      :aria-pressed="card.id === selected"
      :tabindex="card.hidden ? -1 : 0"
      :style="card.style"
      :title="card.label"
      @click="emit('select', card)"
    >
      <img :src="card.imageUrl" alt="" :loading="card.hidden ? 'lazy' : 'eager'" draggable="false" />
    </button>
  </div>
</template>

<style scoped lang="less">
.template-carousel {
  position: relative;
  container-type: inline-size;
  width: min(100%, 422px);
  height: clamp(118px, 29cqh, 178px);
  transform: translateY(7px);
}

.template-card {
  position: absolute;
  top: 50%;
  left: 50%;
  width: min(25cqw, 96px);
  aspect-ratio: 0.78;
  overflow: hidden;
  padding: 0;
  border: 0;
  border-radius: 12px;
  background: var(--gray-100);
  cursor: pointer;
  opacity: var(--card-opacity);
  transform: translate(calc(-50% + var(--offset)), calc(-50% + var(--shift)))
    rotate(var(--rotation)) scale(var(--scale));
  transition: transform 500ms cubic-bezier(0.22, 1, 0.36, 1), opacity 300ms, box-shadow 250ms;

  img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: cover;
  }

  &:hover, &:focus-visible {
    z-index: 5 !important;
    opacity: 1;
    box-shadow: 0 5px 20px #0003;
  }
}

.template-card.is-hidden {
  opacity: 0;
  pointer-events: none;
}

@media (prefers-reduced-motion: reduce) {
  .template-card { transition: none; }
}
</style>
