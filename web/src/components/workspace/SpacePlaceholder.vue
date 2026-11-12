<template>
  <section v-if="view === 'deliverables'" class="space-placeholder deliverables-page">
    <div class="deliverables-toolbar">
      <a-input
        class="file-search"
        placeholder="搜索成果文件名"
        aria-label="搜索成果文件名"
        disabled
      >
        <template #prefix><Search :size="16" /></template>
      </a-input>
      <a-select class="file-filter" value="all" disabled :options="[{ value: 'all', label: '全部文件类型' }]" />
      <span class="toolbar-description">按文件名筛选成果</span>
      <a-button class="refresh-button lucide-icon-btn" disabled><template #icon><RefreshCw :size="16" /></template>刷新</a-button>
    </div>
    <div class="deliverables-selection">
      <a-checkbox disabled>全选</a-checkbox>
      <a-button size="small" disabled><template #icon><FolderUp :size="14" /></template>归档</a-button>
      <span class="placeholder-notice">成果管理尚未开放，以下为示例文件</span>
    </div>
    <div class="deliverables-table-scroll">
      <table class="deliverables-table" aria-label="成果示例文件">
        <thead><tr><th class="selection-column"><span class="sr-only">选择</span></th><th scope="col">文件名</th><th scope="col">归档位置</th><th scope="col">类型</th><th scope="col">大小</th><th scope="col">创建时间</th><th class="actions-column"><span class="sr-only">操作</span></th></tr></thead>
        <tbody><tr v-for="file in sampleFiles" :key="file.id">
          <td class="selection-column"><a-checkbox disabled :aria-label="`选择 ${file.name}`" /></td>
          <td><div class="sample-file"><span class="markdown-icon">MD</span><div class="sample-file-details"><span class="sample-file-name">{{ file.name }}</span><span class="sample-file-archives"><span v-for="provider in file.providers" :key="provider"><CloudUpload :size="12" />{{ provider }}<ArrowUpRight :size="12" /></span></span></div></div></td>
          <td class="muted"><Archive :size="14" /> 未归档</td>
          <td class="muted">md</td><td class="muted">{{ file.size }}</td><td class="muted">{{ file.createdAt }}</td>
          <td><button type="button" class="file-more" disabled :aria-label="`${file.name} 更多操作`"><Ellipsis :size="18" /></button></td>
        </tr></tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import {
  Archive,
  ArrowUpRight,
  CloudUpload,
  Ellipsis,
  FolderUp,
  RefreshCw,
  Search
} from '@lucide/vue'

defineProps({ view: { type: String, required: true } })
const sampleFiles = [
  {
    id: 'sample-1',
    name: 'WPS云文档概览.md',
    providers: ['WPS'],
    size: '2.58 KB',
    createdAt: '2026/10/8 22:56:18'
  },
  {
    id: 'sample-2',
    name: 'WPS云文档文件概览.md',
    providers: ['WPS'],
    size: '2.04 KB',
    createdAt: '2026/10/8 20:31:32'
  },
  {
    id: 'sample-4',
    name: 'cloud-acceptance.md',
    providers: ['WPS'],
    size: '52 B',
    createdAt: '2026/10/6 18:25:59'
  }
]
</script>

<style scoped lang="less">
.space-placeholder {
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: var(--gray-0);
  color: var(--gray-700);
}

.deliverables-page {
  padding: 20px var(--page-padding);
  overflow: auto;
}

.deliverables-toolbar,
.deliverables-selection {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.file-search {
  width: 290px;
}
.file-filter {
  width: 145px;
}
.toolbar-description {
  font-size: 12px;
  color: var(--gray-500);
}
.refresh-button {
  margin-left: auto;
}
.deliverables-selection {
  margin: 18px 0 12px;
  font-size: 14px;
}
.placeholder-notice {
  color: var(--gray-500);
  font-size: 12px;
}
.deliverables-selection .placeholder-notice {
  margin-left: auto;
}
.deliverables-table-scroll {
  overflow-x: auto;
}

.deliverables-table {
  width: 100%;
  min-width: 900px;
  border-collapse: collapse;
  text-align: left;
  font-size: 13px;

  th {
    height: 42px;
    font-weight: 400;
    color: var(--gray-500);
    white-space: nowrap;
  }
  th,
  td {
    padding: 0 12px;
    border-bottom: 1px solid var(--gray-100);
  }
  td {
    height: 82px;
  }
  th:nth-child(2) {
    width: 47%;
  }
  th:nth-child(3) {
    width: 20%;
  }
  th:nth-child(4) {
    min-width: 56px;
  }
  th:nth-child(5) {
    min-width: 88px;
  }
  th:nth-child(6),
  td:nth-child(6) {
    white-space: nowrap;
  }
  tbody tr:hover {
    background: var(--gray-10);
  }
  .selection-column {
    width: 30px;
    padding-left: 8px;
    padding-right: 0;
  }
  .actions-column {
    width: 40px;
  }
}

.sample-file {
  display: flex;
  align-items: center;
  gap: 14px;
}
.sample-file-details {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sample-file-name {
  color: var(--gray-900);
  font-size: 14px;
}
.sample-file-archives {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  color: var(--gray-500);
  font-size: 12px;
}
.sample-file-archives > span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.muted {
  color: var(--gray-500);
  white-space: nowrap;
}

.markdown-icon {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 24px;
  height: 28px;
  border-radius: 3px;
  background: var(--color-accent-500);
  color: var(--gray-0);
  font-size: 8px;
  font-weight: 600;
  clip-path: polygon(0 0, 70% 0, 100% 26%, 100% 100%, 0 100%);
}

.file-more {
  display: inline-flex;
  padding: 4px;
  border: 0;
  background: transparent;
  color: var(--gray-500);
  cursor: not-allowed;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

@media (max-width: 768px) {
  .deliverables-page {
    padding: 16px;
  }
  .file-search {
    width: 100%;
  }
  .refresh-button,
  .deliverables-selection .placeholder-notice {
    margin-left: 0;
  }
}
</style>
