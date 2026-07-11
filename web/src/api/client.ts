import axios from 'axios'
import type { TabTable } from '../components/CrosstabTable'

// '/api' works via vite's dev proxy (see vite.config.ts) when frontend and
// backend run on the same origin locally. Once deployed separately (e.g.
// frontend on Vercel, backend on Render), there's no dev proxy, so
// VITE_API_URL must point at the backend's real URL — set at build time,
// baked into the static bundle (standard Vite env var convention).
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ? `${import.meta.env.VITE_API_URL}/api` : '/api',
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

export interface ProfileVariable {
  name: string
  label: string
  type: string
  missing_pct: number
  distinct_count: number
  min?: number | string | null
  max?: number | string | null
  top_values?: Array<{ value: unknown; count: number }>
}

export interface StudyProfile {
  ready: boolean
  variables: ProfileVariable[]
  summary: {
    n_variables: number
    n_respondents: number
    n_high_missing: number
    n_constant: number
    n_likely_duplicate_id: number
  }
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

  emergencyReset: async (email: string, token: string, newPassword: string) => {
    return api.post<{ status: string; message: string }>('/auth/emergency-reset', {
      email,
      token,
      new_password: newPassword,
    })
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

  profile: async (id: string) => {
    return api.get<StudyProfile>(`/studies/${id}/profile`)
  },

  approve: async (id: string, gate: string) => {
    return api.post(`/studies/${id}/approve/${gate}`, {})
  },

  reprocess: async (id: string) => {
    return api.post<{ study_id: string; status: string; source: string }>(
      `/studies/${id}/reprocess`,
      {},
    )
  },

  archive: async (id: string) => {
    return api.delete(`/studies/${id}`)
  },

  downloadUrl: (id: string, fmt: 'excel' | 'pptx') => `/api/studies/${id}/download/${fmt}`,
}

// --- Questionnaire review types ---------------------------------------------
// Mirrors src/core/survey_definition.py's Expression/Option/Question shapes and
// the questionnaire route payloads (api/routes/questionnaire.py).

export type ExprOp = 'eq' | 'ne' | 'in' | 'gt' | 'lt' | 'and' | 'or' | 'not' | 'selected'

export interface Expression {
  op: ExprOp
  qid?: string | null
  value?: unknown
  values?: unknown[] | null
  children?: Expression[] | null
  verbatim_text?: string | null
}

export interface QuestionOption {
  code: number
  label: string
  exclusive?: boolean
  anchored?: boolean
  open_other?: boolean
}

export interface Question {
  qid: string
  text: string
  type: string
  options: QuestionOption[]
  rows: QuestionOption[] | null
  cols: QuestionOption[] | null
  required: boolean
  randomize_options: boolean
  show_if: Expression | null
  terminate_if: Expression | null
  piping_refs: string[]
  validation: Record<string, unknown> | null
  source_text: string | null
  source_location: string | null
  extraction_confidence: Record<string, number>
  flags: string[]
  approved: boolean
}

export interface QuestionnaireStatus {
  id: string
  study_id: string
  status: string
  source_filename: string
  version: number
  created_at: string
  updated_at: string
  title: string | null
  n_questions: number
  n_approved: number
  source?: string | null // 'extracted' | 'authored'
  validation_errors: string[]
  extraction_summary: Record<string, unknown>
  error_detail?: string | null
}

export interface QuestionsListResponse {
  questionnaire_id: string
  study_id: string
  status: string
  questions: Question[]
}

export interface QuestionUpdate {
  qid?: string | null // rename (builder); backend rewrites references consistently
  text?: string | null
  type?: string | null
  options?: Array<Record<string, unknown>> | null
  rows?: Array<Record<string, unknown>> | null
  cols?: Array<Record<string, unknown>> | null
  required?: boolean | null
  show_if?: Record<string, unknown> | null
  terminate_if?: Record<string, unknown> | null
}

export interface QuestionCreate {
  qid: string
  text?: string
  type?: string
  options?: Array<Record<string, unknown>>
  rows?: Array<Record<string, unknown>> | null
  cols?: Array<Record<string, unknown>> | null
  required?: boolean
  show_if?: Record<string, unknown> | null
  terminate_if?: Record<string, unknown> | null
}

export interface UnresolvedQuestion {
  qid: string
  reasons: string[]
}

export const QUESTION_TYPES = [
  'single',
  'multi',
  'dropdown',
  'rank',
  'grid_single',
  'numeric',
  'open_text',
  'info',
] as const

// --- Verbatim coding review queue -------------------------------------------

export interface CodeDef {
  id: string
  label: string
  description: string
  type: string
  parent_id: string | null
  examples: string[]
}

export interface ReviewItem {
  respondent_id: number
  verbatim: string
  suggested_codes: string[]
  confidence: number
  reasoning: string
  status: 'pending' | 'accepted' | 'corrected'
  final_codes?: string[]
  resolved_by?: string
}

export interface CodingVariable {
  question_var: string
  label?: string
  note?: string // present when coding never ran for this variable
  codeframe: { allow_multi: boolean; version: number; codes: CodeDef[] }
  summary: { n_auto_coded: number; n_needs_review: number; confidence_mean: number }
  review_queue: ReviewItem[]
}

export type CodingPayload = Record<string, CodingVariable>

export const codingApi = {
  get: async (studyId: string) => api.get<CodingPayload>(`/studies/${studyId}/coding`),

  resolve: async (
    studyId: string,
    variable: string,
    respondentId: number,
    status: 'accepted' | 'corrected',
    codes?: string[],
  ) =>
    api.patch<{ variable: string; respondent_id: number; status: string }>(
      `/studies/${studyId}/coding/${variable}/${respondentId}`,
      codes === undefined ? { status } : { status, codes },
    ),
}

export const questionnaireApi = {
  status: async (studyId: string) =>
    api.get<QuestionnaireStatus>(`/studies/${studyId}/questionnaire`),

  upload: async (studyId: string, file: File, onProgress?: (pct: number) => void) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post<{ questionnaire_id: string; study_id: string; status: string; message: string }>(
      `/studies/${studyId}/questionnaire/upload`,
      formData,
      {
        onUploadProgress: (e) => {
          if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
        },
        timeout: 0,
      },
    )
  },

  questions: async (studyId: string) =>
    api.get<QuestionsListResponse>(`/studies/${studyId}/questionnaire/questions`),

  question: async (studyId: string, qid: string) =>
    api.get<Question>(`/studies/${studyId}/questionnaire/questions/${qid}`),

  update: async (studyId: string, qid: string, patch: QuestionUpdate) =>
    api.patch<Question>(`/studies/${studyId}/questionnaire/questions/${qid}`, patch),

  approve: async (studyId: string, qid: string) =>
    api.post<{ qid: string; approved: boolean; n_approved: number }>(
      `/studies/${studyId}/questionnaire/questions/${qid}/approve`,
      {},
    ),

  approveClean: async (studyId: string) =>
    api.post<{
      n_newly_approved: number
      newly_approved: string[]
      n_approved: number
      still_blocked: { qid: string; flags: string[] }[]
    }>(`/studies/${studyId}/questionnaire/questions/approve-clean`, {}),

  approveAll: async (studyId: string) =>
    api.post<{ questionnaire_id: string; status: string }>(
      `/studies/${studyId}/questionnaire/approve-all`,
      {},
    ),

  // --- Authoring builder (T12) ---
  createAuthored: async (studyId: string, title: string) =>
    api.post<QuestionnaireStatus>(`/studies/${studyId}/questionnaire/authored`, { title }),

  addQuestion: async (studyId: string, question: QuestionCreate) =>
    api.post<Question>(`/studies/${studyId}/questionnaire/questions`, question),

  deleteQuestion: async (studyId: string, qid: string) =>
    api.delete<{ deleted: string; n_questions: number }>(
      `/studies/${studyId}/questionnaire/questions/${qid}`,
    ),

  reorderQuestions: async (studyId: string, qids: string[]) =>
    api.put<{ order: string[] }>(`/studies/${studyId}/questionnaire/questions/order`, { qids }),
}

// --- Fielding / respondent runtime (T9') ------------------------------------
// The respondent endpoints are PUBLIC (no JWT). The shared `api` instance only
// attaches an Authorization header when a token happens to be in localStorage,
// which the public backend routes ignore — so respondents (who have none) reach
// them fine.

export interface RespondentEntry {
  session_id: string
  status: 'open' | 'closed'
  surveyjs_json: Record<string, unknown>
  current_page: number
  // Set only on a terminal-at-entry outcome (duplicate-pid security = 30).
  status_code?: number | null
  redirect_url?: string | null
}

export interface RespondentSession {
  session_id: string
  status_code: number | null
  current_page: number
  answers: Record<string, unknown>
  completed_at: string | null
}

export interface AnswersResult {
  ok?: boolean
  terminated?: boolean
  status_code?: number | null
  already_final?: boolean
  redirect_url?: string | null
}

// --- Fielding management (T13, authenticated) -------------------------------
// Mirrors api/routes/fielding.py + materialize.py. `counts` keys match
// db.fielding_link_counts: starts / completes / terminates / security / overquota.

export interface FieldingRedirects {
  redirect_complete?: string | null
  redirect_terminate?: string | null
  redirect_overquota?: string | null
  redirect_security?: string | null
}

export interface FieldingLink extends FieldingRedirects {
  id: string
  study_id: string
  questionnaire_id: string
  token: string
  status: 'open' | 'closed'
  survey_version: number
  created_by: string
  created_at: string
  closed_at: string | null
  respondent_path: string
  counts: {
    starts?: number
    completes?: number
    terminates?: number
    security?: number
    overquota?: number
  }
}

export interface QuotaCell {
  id: string
  fielding_link_id: string
  name: string
  condition: Expression
  limit: number
  count: number
}

export interface MaterializeResult {
  n_respondents: number
  n_variables: number
  destination: string
}

export const fieldingApi = {
  // `search` is window.location.search (e.g. "?pid=P1&rid=R2") so the backend
  // captures panel params into the session's entry_params at entry time.
  enter: async (token: string, search = '') =>
    api.get<RespondentEntry>(`/r/${token}${search}`),

  answers: async (
    token: string,
    sessionId: string,
    answers: Record<string, unknown>,
    currentPage: number,
  ) =>
    api.post<AnswersResult>(`/r/${token}/sessions/${sessionId}/answers`, {
      answers,
      current_page: currentPage,
    }),

  complete: async (token: string, sessionId: string) =>
    api.post<{ status_code: number | null; complete: boolean; redirect_url?: string | null }>(
      `/r/${token}/sessions/${sessionId}/complete`,
      {},
    ),

  resume: async (token: string, sessionId: string) =>
    api.get<RespondentSession>(`/r/${token}/sessions/${sessionId}`),

  // --- Management (authenticated) ---
  listLinks: async (studyId: string) =>
    api.get<{ study_id: string; links: FieldingLink[] }>(`/studies/${studyId}/fielding/links`),

  createLink: async (studyId: string, redirects?: FieldingRedirects) =>
    api.post<FieldingLink>(`/studies/${studyId}/fielding/links`, redirects ?? {}),

  closeLink: async (studyId: string, linkId: string) =>
    api.post<FieldingLink>(`/studies/${studyId}/fielding/links/${linkId}/close`, {}),

  listQuotas: async (studyId: string, linkId: string) =>
    api.get<{ link_id: string; cells: QuotaCell[] }>(
      `/studies/${studyId}/fielding/links/${linkId}/quotas`,
    ),

  createQuota: async (
    studyId: string,
    linkId: string,
    cell: { name: string; condition: Record<string, unknown>; limit: number },
  ) =>
    api.post<{ link_id: string; cells: QuotaCell[] }>(
      `/studies/${studyId}/fielding/links/${linkId}/quotas`,
      { cells: [cell] },
    ),

  updateQuota: async (
    studyId: string,
    linkId: string,
    quotaId: string,
    patch: { limit?: number; condition?: Record<string, unknown> },
  ) =>
    api.patch<QuotaCell>(
      `/studies/${studyId}/fielding/links/${linkId}/quotas/${quotaId}`,
      patch,
    ),

  materialize: async (studyId: string, linkId: string) =>
    api.post<MaterializeResult>(
      `/studies/${studyId}/fielding/links/${linkId}/materialize`,
      {},
    ),
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
