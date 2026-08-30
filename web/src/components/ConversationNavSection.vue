<template>
  <section class="conversation-nav-section" :class="{ collapsed }">
    <div v-if="showHistory && !collapsed" class="history-panel">
      <div class="view-switch" role="group" aria-label="对话展示方式">
        <button
          type="button"
          :class="{ active: viewMode === 'projects' }"
          :aria-pressed="viewMode === 'projects'"
          @click="viewMode = 'projects'"
        >
          项目
        </button>
        <button
          type="button"
          :class="{ active: viewMode === 'recent' }"
          :aria-pressed="viewMode === 'recent'"
          @click="viewMode = 'recent'"
        >
          最近
        </button>
      </div>

      <div v-if="viewMode === 'projects'" class="conversation-list project-list">
        <div v-if="projectsLoading" class="list-state">正在加载项目...</div>
        <div v-else-if="projectsError" class="list-state list-error" role="alert">
          <span>项目加载失败</span>
          <button type="button" @click="$emit('retry-projects')">重试</button>
        </div>
        <template v-else>
          <section v-for="group in projectGroups" :key="group.project.id" class="project-group">
            <div class="project-row" :class="{ pending: projectPendingId === group.project.id }">
              <button
                type="button"
                class="project-toggle"
                :aria-expanded="isProjectExpanded(group.project.id)"
                @click="toggleProject(group.project.id)"
              >
                <ChevronRight
                  :size="14"
                  class="project-chevron"
                  :class="{ expanded: isProjectExpanded(group.project.id) }"
                />
                <Folder :size="17" />
                <span class="project-name">{{ group.project.name }}</span>
                <span class="project-count">{{ group.conversations.length }}</span>
              </button>
              <a-dropdown :trigger="['click']" :disabled="projectPendingId === group.project.id">
                <template #overlay>
                  <a-menu>
                    <a-menu-item
                      key="rename"
                      :icon="h(SquarePen, { size: 14 })"
                      @click="renameProject(group.project)"
                      >重命名项目</a-menu-item
                    >
                    <a-menu-item
                      key="delete"
                      danger
                      :icon="h(Trash2, { size: 14 })"
                      @click="confirmDeleteProject(group.project)"
                      >删除项目</a-menu-item
                    >
                  </a-menu>
                </template>
                <button type="button" class="project-more" aria-label="项目操作" @click.stop>
                  <MoreVertical :size="16" />
                </button>
              </a-dropdown>
            </div>
            <div v-show="isProjectExpanded(group.project.id)" class="project-conversations">
              <ConversationNavItem
                v-for="chat in group.conversations"
                :key="chat.id"
                :chat="chat"
                :current-chat-id="currentChatId"
                nested
                @select-chat="$emit('select-chat', $event)"
                @delete-chat="$emit('delete-chat', $event)"
                @rename-chat="$emit('rename-chat', $event)"
                @toggle-pin="$emit('toggle-pin', $event)"
              />
              <div v-if="!group.conversations.length" class="project-empty">暂无对话</div>
            </div>
          </section>

          <section v-if="otherConversations.length" class="other-conversations">
            <button
              type="button"
              class="other-heading"
              :aria-expanded="otherExpanded"
              @click="otherExpanded = !otherExpanded"
            >
              <ChevronRight
                :size="14"
                class="project-chevron"
                :class="{ expanded: otherExpanded }"
              />
              <MessagesSquare :size="17" />
              <span>其他对话</span>
              <span class="project-count">{{ otherConversations.length }}</span>
            </button>
            <div v-show="otherExpanded">
              <ConversationNavItem
                v-for="chat in otherConversations"
                :key="chat.id"
                :chat="chat"
                :current-chat-id="currentChatId"
                nested
                @select-chat="$emit('select-chat', $event)"
                @delete-chat="$emit('delete-chat', $event)"
                @rename-chat="$emit('rename-chat', $event)"
                @toggle-pin="$emit('toggle-pin', $event)"
              />
            </div>
          </section>

          <div v-if="!projectGroups.length && !otherConversations.length" class="list-state">
            暂无项目或对话
          </div>
          <button
            v-if="hasMoreChats"
            type="button"
            class="load-more-btn"
            :disabled="isLoadingMore"
            @click="$emit('load-more-chats')"
          >
            {{ isLoadingMore ? '加载中...' : '加载更多' }}
          </button>
        </template>
      </div>

      <div v-else class="conversation-list">
        <ConversationNavItem
          v-for="chat in sortedChats"
          :key="chat.id"
          :chat="chat"
          :current-chat-id="currentChatId"
          @select-chat="$emit('select-chat', $event)"
          @delete-chat="$emit('delete-chat', $event)"
          @rename-chat="$emit('rename-chat', $event)"
          @toggle-pin="$emit('toggle-pin', $event)"
        />
        <div v-if="!sortedChats.length" class="list-state">暂无对话历史</div>
        <button
          v-if="hasMoreChats"
          type="button"
          class="load-more-btn"
          :disabled="isLoadingMore"
          @click="$emit('load-more-chats')"
        >
          {{ isLoadingMore ? '加载中...' : '加载更多' }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, h, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { ChevronRight, Folder, MessagesSquare, MoreVertical, SquarePen, Trash2 } from '@lucide/vue'
import ConversationNavItem from '@/components/ConversationNavItem.vue'
import { buildProjectConversationGroups } from '@/utils/projectConversationGroups'

const props = defineProps({
  currentChatId: { type: String, default: null },
  chatsList: { type: Array, default: () => [] },
  projects: { type: Array, default: () => [] },
  projectsLoading: { type: Boolean, default: false },
  projectsError: { type: String, default: '' },
  projectPendingId: { type: String, default: null },
  hasMoreChats: { type: Boolean, default: false },
  isLoadingMore: { type: Boolean, default: false },
  collapsed: { type: Boolean, default: false },
  showHistory: { type: Boolean, default: true }
})

const emit = defineEmits([
  'select-chat',
  'delete-chat',
  'rename-chat',
  'toggle-pin',
  'load-more-chats',
  'rename-project',
  'delete-project',
  'retry-projects'
])
const viewMode = ref('projects')
const collapsedProjects = ref(new Set())
const otherExpanded = ref(true)
const groupedNavigation = computed(() =>
  buildProjectConversationGroups(props.projects, props.chatsList)
)
const projectGroups = computed(() => groupedNavigation.value.groups)
const otherConversations = computed(() => groupedNavigation.value.otherConversations)
const sortedChats = computed(() => groupedNavigation.value.sortedConversations)

const isProjectExpanded = (projectId) => !collapsedProjects.value.has(projectId)
const toggleProject = (projectId) => {
  const next = new Set(collapsedProjects.value)
  if (next.has(projectId)) next.delete(projectId)
  else next.add(projectId)
  collapsedProjects.value = next
}

const renameProject = (project) => {
  let name = project.name || ''
  Modal.confirm({
    title: '重命名项目',
    icon: null,
    centered: true,
    width: 400,
    content: h('input', {
      value: name,
      class: 'rename-conversation-input',
      'aria-label': '项目名称',
      onInput: (event) => {
        name = event.target.value
      }
    }),
    okText: '保存',
    cancelText: '取消',
    onOk: () => {
      if (!name.trim()) {
        message.warning('项目名称不能为空')
        return Promise.reject()
      }
      emit('rename-project', { projectId: project.id, name })
    }
  })
}

const confirmDeleteProject = (project) => {
  Modal.confirm({
    title: `删除项目“${project.name}”？`,
    icon: null,
    centered: true,
    okText: '删除项目',
    okButtonProps: { danger: true },
    cancelText: '取消',
    content: '项目中的对话会被删除，项目文件夹和其中的文件会保留。',
    onOk: () => emit('delete-project', project.id)
  })
}
</script>

<style lang="less" scoped>
.conversation-nav-section {
  display: flex;
  min-height: 0;
  flex-direction: column;
  margin-top: 8px;
  overflow: hidden;
}
.history-panel {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
}
.view-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  margin: 0 4px 6px;
  padding: 2px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-25);
  button {
    height: 26px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-500);
    cursor: pointer;
    font-size: 12px;
    transition:
      background-color 0.15s ease,
      color 0.15s ease;
    &:hover {
      color: var(--gray-800);
    }
    &:focus-visible {
      outline: 2px solid var(--main-300);
      outline-offset: 1px;
    }
    &.active {
      background: var(--gray-0);
      color: var(--gray-900);
      font-weight: 600;
    }
  }
}
.conversation-list {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  padding-right: 2px;
  scrollbar-width: thin;
}
.project-group + .project-group,
.other-conversations {
  margin-top: 2px;
}
.project-row {
  display: flex;
  align-items: center;
  min-height: 34px;
  border-radius: 8px;
  color: var(--gray-800);
  &:hover {
    background: var(--gray-50);
    .project-more {
      opacity: 1;
    }
  }
  &.pending {
    opacity: 0.55;
    pointer-events: none;
  }
}
.project-toggle,
.other-heading {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 4px 0 7px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  font-size: 13px;
  text-align: left;
  &:focus-visible {
    border-radius: 7px;
    outline: 2px solid var(--main-300);
    outline-offset: -2px;
  }
}
.project-chevron {
  flex: 0 0 auto;
  color: var(--gray-400);
  transition: transform 0.15s ease;
  &.expanded {
    transform: rotate(90deg);
  }
}
.project-name,
.other-heading span:not(.project-count) {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.project-count {
  margin-left: auto;
  color: var(--gray-400);
  font-size: 11px;
}
.project-more {
  display: inline-flex;
  flex: 0 0 28px;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  opacity: 0;
  &:focus-visible {
    opacity: 1;
    outline: 2px solid var(--main-300);
  }
}
.project-empty {
  padding: 3px 8px 7px 30px;
  color: var(--gray-400);
  font-size: 12px;
}
.other-heading {
  width: 100%;
  color: var(--gray-600);
}
.list-state {
  padding: 18px 8px;
  color: var(--gray-500);
  font-size: 12px;
  text-align: center;
}
.list-error {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--color-error-700);
  button {
    border: 0;
    background: transparent;
    color: var(--main-color);
    cursor: pointer;
  }
}
.load-more-btn {
  display: block;
  margin: 8px auto;
  border: 0;
  background: transparent;
  color: var(--main-color);
  cursor: pointer;
  font-size: 12px;
}
@media (hover: none) {
  .project-more {
    opacity: 1;
  }
}
</style>
