export function createScheduledAgentAutosave({ persist, onPersisted, onState, delay = 600 }) {
  let timer = null
  let latest = null
  let active = null
  let draftToken = 0
  let draftJobId = null
  let revision = 0
  let state = 'idle'

  function publish(nextState, error = '') {
    state = nextState
    onState({ state: nextState, saving: nextState === 'saving', error })
  }

  function clearTimer() {
    if (timer === null) return
    clearTimeout(timer)
    timer = null
  }

  async function save(saveRequest) {
    publish('saving')
    try {
      const savedJob = await persist(saveRequest)
      let finalizeDraft = false
      if (!saveRequest.jobId) {
        draftJobId = savedJob.id
        if (latest?.draftToken === saveRequest.draftToken && !latest.jobId) {
          latest.jobId = savedJob.id
        }
      }
      if (
        saveRequest.draftToken === draftToken &&
        saveRequest.revision === revision &&
        (!draftJobId || savedJob.id === draftJobId)
      ) {
        finalizeDraft = Boolean(draftJobId)
      }
      onPersisted(savedJob, {
        created: !saveRequest.jobId,
        finalizeDraft
      })
      if (finalizeDraft) draftJobId = null
      if (saveRequest.revision === revision) publish('saved')
      return true
    } catch (error) {
      if (saveRequest.revision === revision) {
        if (!latest) latest = saveRequest
        publish('error', `${error.message || '自动保存失败'}，请重试后再离开`)
      }
      return false
    }
  }

  function canLeave() {
    return state !== 'error' && state !== 'invalid'
  }

  async function flush() {
    clearTimer()
    if (active) {
      const saved = await active
      if (!saved) return false
      return latest ? flush() : canLeave()
    }
    if (!latest) return canLeave()

    const saveRequest = latest
    latest = null
    const promise = save(saveRequest)
    active = promise
    const saved = await promise
    if (active === promise) active = null
    if (!saved) return false
    return latest ? flush() : canLeave()
  }

  function queue({ payload, error }, selectedJobId) {
    clearTimer()
    revision += 1
    if (error || !payload) {
      latest = null
      publish('invalid')
      return
    }
    latest = {
      payload,
      jobId: selectedJobId || draftJobId,
      draftToken,
      revision
    }
    publish('dirty')
    timer = setTimeout(() => void flush(), delay)
  }

  function beginDraft() {
    draftToken += 1
    draftJobId = null
    publish('idle')
  }

  function leaveEditor() {
    clearTimer()
    latest = null
    draftJobId = null
    publish('idle')
  }

  return { beginDraft, flush, leaveEditor, queue }
}

export async function canLeaveScheduledTab(currentTab, nextTab, flush) {
  if (currentTab !== 'schedules' || nextTab === 'schedules') return true
  return flush()
}
