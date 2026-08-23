<template>
  <aside class="workspace-sidebar">
    <div class="sidebar-actions">
      <button
        type="button"
        class="sidebar-action-btn"
        :disabled="disabled || uploading"
        :title="disabled ? '当前目录不支持上传文件' : '上传文件'"
        @click="$emit('upload-file')"
      >
        <Loader2 v-if="uploading" class="action-spinner" :size="16" />
        <Upload v-else :size="16" />
        <span>{{ uploading ? '上传中...' : '上传文件' }}</span>
      </button>
      <button
        type="button"
        class="sidebar-action-btn"
        :disabled="disabled || uploading"
        :title="disabled ? '当前目录不支持新建文件夹' : '新建文件夹'"
        @click="$emit('create-directory')"
      >
        <FolderPlus :size="16" />
        <span>新建文件夹</span>
      </button>
    </div>

    <section class="sidebar-section">
      <button
        type="button"
        class="workspace-nav-item"
        :class="{ active: activeKey === 'personal' && !isQuickAccessPath(currentPath) }"
        @click="$emit('select-personal')"
      >
        <FileTypeIcon is-dir folder-variant="personal" :size="16" />
        <span>个人空间</span>
      </button>
    </section>

    <section class="sidebar-section">
      <div class="section-title">快速访问</div>
      <button
        type="button"
        class="workspace-nav-item secondary"
        :class="{ active: activeKey === 'personal' && isSamePath(currentPath, agentsPath) }"
        @click="$emit('select-path', agentsPath)"
      >
        <FileTypeIcon is-dir :size="16" />
        <span>智能体文件</span>
      </button>
      <button
        type="button"
        class="workspace-nav-item secondary"
        :class="{
          active: activeKey === 'personal' && isSamePath(currentPath, savedArtifactsPath)
        }"
        @click="$emit('select-path', savedArtifactsPath)"
      >
        <FileTypeIcon is-dir :size="16" />
        <span>保存的产物</span>
      </button>
    </section>

    <section v-if="knowledgeEnabled && myDatabases.length" class="sidebar-section">
      <div class="section-title">我的知识库</div>
      <button
        v-for="database in myDatabases"
        :key="database.kb_id || database.id || database.name"
        type="button"
        class="workspace-nav-item secondary"
        :class="{ active: activeKey === `database:${database.kb_id}` }"
        @click="$emit('select-database', database)"
      >
        <FileTypeIcon is-dir :size="16" />
        <span>{{ database.name }}</span>
      </button>
    </section>

    <section v-if="knowledgeEnabled && sharedDatabases.length" class="sidebar-section">
      <div class="section-title">共享知识库</div>
      <button
        v-for="database in sharedDatabases"
        :key="database.kb_id || database.id || database.name"
        type="button"
        class="workspace-nav-item secondary"
        :class="{ active: activeKey === `database:${database.kb_id}` }"
        @click="$emit('select-database', database)"
      >
        <FileTypeIcon is-dir :size="16" />
        <span>{{ database.name }}</span>
      </button>
    </section>

    <section v-if="knowledgeEnabled && loadingDatabases" class="sidebar-section">
      <div class="sidebar-muted">正在加载知识库...</div>
    </section>
    <section v-else-if="knowledgeEnabled && !databases.length" class="sidebar-section">
      <div class="sidebar-muted">暂无可访问知识库</div>
    </section>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { FolderPlus, Loader2, Upload } from 'lucide-vue-next'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'

const savedArtifactsPath = '/saved_artifacts'
const agentsPath = '/agents/'
const quickAccessPaths = [savedArtifactsPath, agentsPath]

const normalizePath = (path) => String(path || '/').replace(/\/$/, '') || '/'
const isSameOrChildPath = (path, targetPath) => {
  const current = normalizePath(path)
  const target = normalizePath(targetPath)
  return current === target || current.startsWith(`${target}/`)
}
const isSamePath = (path, targetPath) => normalizePath(path) === normalizePath(targetPath)
const isQuickAccessPath = (path) =>
  quickAccessPaths.some((targetPath) => isSameOrChildPath(path, targetPath))

const props = defineProps({
  activeKey: { type: String, default: 'personal' },
  currentPath: { type: String, default: '/' },
  databases: { type: Array, default: () => [] },
  loadingDatabases: { type: Boolean, default: false },
  knowledgeEnabled: { type: Boolean, default: false },
  currentUid: { type: String, default: '' },
  disabled: { type: Boolean, default: false },
  uploading: { type: Boolean, default: false }
})

defineEmits(['select-personal', 'select-database', 'select-path', 'upload-file', 'create-directory'])

const myDatabases = computed(() =>
  props.databases.filter((db) => db.created_by === props.currentUid)
)

const sharedDatabases = computed(() =>
  props.databases.filter((db) => db.created_by !== props.currentUid)
)
</script>

<style scoped lang="less">
.workspace-sidebar {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  padding: 8px 8px 12px;
  border-right: 1px solid var(--gray-100);
  background: var(--gray-0);
  overflow-y: auto;
}

.sidebar-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-bottom: 2px;
}

.sidebar-action-btn {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 7px;
  width: 100%;
  height: 30px;
  padding: 0 8px;
  border-radius: 6px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  outline: none;
  transition: all 0.16s ease;

  span {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &:hover:not(:disabled) {
    background: var(--gray-50);
    border-color: var(--gray-200);
    color: var(--gray-2000);
  }

  &:active:not(:disabled) {
    background: var(--gray-100);
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
    box-shadow: none;
  }
}

.action-spinner {
  animation: action-spin 1s linear infinite;
}

@keyframes action-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.sidebar-section {
  display: flex;
  flex-direction: column;
}

.section-title {
  padding: 3px 6px;
  color: var(--gray-500);
  font-size: 11px;
  font-weight: 600;
  line-height: 18px;
}

.workspace-nav-item {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  min-height: 32px;
  padding: 0 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-700);
  font-size: 13px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition:
    background-color 0.2s ease,
    color 0.2s ease,
    border-color 0.2s ease;

  span:first-of-type {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &:hover:not(:disabled) {
    border-color: transparent;
    color: var(--gray-2000);
    background-color: var(--gray-50);
  }

  &.active {
    border-color: transparent;
    color: var(--gray-2000);
    background-color: color-mix(in srgb, var(--gray-800) 6%, var(--gray-0));
    font-weight: 600;
  }

  &.secondary {
    min-height: 28px;
    font-size: 12.5px;
  }
}

.sidebar-muted {
  padding: 4px 6px;
  color: var(--gray-500);
  font-size: 11.5px;
  line-height: 1.5;
}
</style>
