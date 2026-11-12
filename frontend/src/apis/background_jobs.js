import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

const BASE_URL = '/api/background-jobs'

export const backgroundJobsApi = {
  fetchJobs: async (params = {}) => {
    const query = new URLSearchParams(params).toString()
    const url = query ? `${BASE_URL}?${query}` : BASE_URL
    return apiAdminGet(url)
  },

  fetchJobDetail: async (jobId) => {
    return apiAdminGet(`${BASE_URL}/${jobId}`)
  },

  cancelJob: async (jobId) => {
    return apiAdminPost(`${BASE_URL}/${jobId}/cancel`, {})
  },

  deleteJob: async (jobId) => {
    return apiAdminDelete(`${BASE_URL}/${jobId}`)
  }
}
