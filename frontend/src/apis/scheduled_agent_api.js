import { apiDelete, apiGet, apiPost, apiRequest } from './base'

/** 用户 Agent 定时任务 API。 */
export const scheduledAgentApi = {
  list: () => apiGet('/api/scheduled-tasks'),
  history: (jobId, { limit = 10, offset = 0 } = {}) =>
    apiGet(`/api/scheduled-tasks/${encodeURIComponent(jobId)}/runs?limit=${limit}&offset=${offset}`),
  create: (payload) => apiPost('/api/scheduled-tasks', payload),
  update: (jobId, payload) =>
    apiRequest(`/api/scheduled-tasks/${jobId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload)
    }),
  runNow: (jobId, requestId) =>
    apiPost(`/api/scheduled-tasks/${jobId}/run-now`, { request_id: requestId }),
  remove: (jobId) => apiDelete(`/api/scheduled-tasks/${jobId}`)
}
