import { apiGet, apiPost } from './base'

const MESSAGE_ACTIVITY_PATH = '/api/user/message-activity'

export const userApi = {
  uploadImage: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiPost('/api/user/upload-image', formData)
  },

  getMessageActivity: () => apiGet(MESSAGE_ACTIVITY_PATH)
}
