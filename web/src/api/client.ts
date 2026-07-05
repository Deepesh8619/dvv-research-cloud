import axios from 'axios'
import type { TabTable } from '../components/CrosstabTable'

const api = axios.create({
  baseURL: '/api',
})

// Request interceptor: attach JWT token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('authToken')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// Response interceptor: handle 401 and clear auth
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('authToken')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export interface Study {
  id: string
  client_name: string
  status: string
  created_at: string
  updated_at: string
  metadata: {
    classification_confidence?: number | null
    qc_passed?: boolean | null
    qc_issues?: string[]
    qc_warnings?: string[]
    error?: string | null
  }
  approval_chain: Array<{
    gate: string
    approved_by: string
    approved_at: string
  }>
  excel_path?: string | null
  pptx_path?: string | null
}

export interface StudyListResponse {
  studies: Study[]
  total: number
  page: number
  limit: number
}

export interface CrosstabsPayload {
  title: string
  weight: string | null
  confidence: number
  min_base_for_test: number
  tables: TabTable[]
}

export interface User {
  id: string
  email: string
  name: string
  role: string
  created_at: string
  is_active: boolean
}

export interface ShareLink {
  token: string
  study_id: string
  created_by: string
  created_at: string
  revoked: boolean
}

export interface ChatToolCall {
  tool: string
  params: Record<string, unknown>
}

export interface ChatMessage {
  id: string
  role: string
  content: string
  tools?: ChatToolCall[] | null
  created_at: string
}

export interface ChatResponse {
  message: ChatMessage
  tool_results?: Record<string, unknown>[] | null
  conversation_id: string
}

export interface ChatHistoryResponse {
  study_id: string
  messages: ChatMessage[]
}

export interface PublicStudy {
  client_name: string
  status: string
  created_at: string
  excel_available: boolean
  pptx_available: boolean
}

export const authApi = {
  login: async (email: string, password: string) => {
    // OAuth2PasswordRequestForm expects form-encoded username/password
    const form = new URLSearchParams()
    form.append('username', email)
    form.append('password', password)
    return api.post<{ access_token: string; token_type: string; user: User }>(
      '/auth/login',
      form,
      { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } },
    )
  },

  me: async () => {
    return api.get<User>('/auth/me')
  },

  listUsers: async () => {
    return api.get<User[]>('/auth/users')
  },

  createUser: async (email: string, name: string, role: string, password: string) => {
    return api.post<User>('/auth/users', { email, name, role, password })
  },
}

export const shareApi = {
  create: async (studyId: string) => {
    return api.post<ShareLink>(`/studies/${studyId}/share`)
  },

  revoke: async (studyId: string) => {
    return api.post(`/studies/${studyId}/share/revoke`)
  },

  getPublic: async (token: string) => {
    return api.get<PublicStudy>(`/public/studies/${token}`)
  },

  publicDownloadUrl: (token: string, fmt: 'excel' | 'pptx') => `/api/public/studies/${token}/download/${fmt}`,
}

export const studiesApi = {
  upload: async (file: File, clientName?: string, onProgress?: (pct: number) => void) => {
    const formData = new FormData()
    formData.append('file', file)
    if (clientName) formData.append('client_name', clientName)
    return api.post<{ study_id: string; status: string; message: string }>(
      '/studies/upload',
      formData,
      {
        onUploadProgress: (e) => {
          if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        },
        // Large SPSS files can take a while over a slow connection — the default
        // axios timeout (0 = none) already applies, but be explicit so nobody
        // "fixes" this later by adding a short timeout that breaks big uploads.
        timeout: 0,
      },
    )
  },

  list: async (skip = 0, limit = 100) => {
    return api.get<StudyListResponse>('/studies', { params: { skip, limit } })
  },

  get: async (id: string) => {
    return api.get<Study>(`/studies/${id}`)
  },

  crosstabs: async (id: string) => {
    return api.get<CrosstabsPayload>(`/studies/${id}/crosstabs`)
  },

  approve: async (id: string, gate: string) => {
    return api.post(`/studies/${id}/approve/${gate}`, {})
  },

  archive: async (id: string) => {
    return api.delete(`/studies/${id}`)
  },

  downloadUrl: (id: string, fmt: 'excel' | 'pptx') => `/api/studies/${id}/download/${fmt}`,
}

export const chatApi = {
  send: async (studyId: string, message: string) => {
    return api.post<ChatResponse>(`/studies/${studyId}/chat`, { message })
  },
  history: async (studyId: string, limit = 50) => {
    return api.get<ChatHistoryResponse>(`/studies/${studyId}/chat/history`, { params: { limit } })
  },
}

export default api
