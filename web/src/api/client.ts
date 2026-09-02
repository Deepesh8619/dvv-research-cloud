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
    /** Which phase a running pipeline is in — also its liveness heartbeat. */
    stage?: string | null
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

  // Create a data-less study (survey-first path — no file, no pipeline). Returns
  // the full Study so the caller can navigate straight into questionnaire/builder.
  create: async (clientName: string, kind?: 'questionnaire' | 'builder') => {
    return api.post<Study>('/studies', { client_name: clientName, kind })
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

  /** Check the collected data against the questionnaire it was collected under. */
  crosscheck: async (id: string) => {
    return api.post<CrosscheckReport>(`/studies/${id}/crosscheck`, {})
  },

  /** The last stored cross-check, so leaving and returning does not lose it. */
  storedCrosscheck: async (id: string) => {
    return api.get<{ report: CrosscheckReport | null }>(`/studies/${id}/crosscheck`)
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
  /** How this question was read: "spec_table" | "prose_directives" | "llm".
      Optional: questions stored before provenance existed carry neither. */
  extraction_method?: string | null
  /** field -> the exact fragment that decided it. */
  extraction_evidence?: Record<string, string>
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
  /** True when the study's data map was available to accelerate extraction. */
  used_datamap?: boolean
  n_recovered_from_datamap?: number
  n_approved: number
  source?: string | null // 'extracted' | 'authored'
  validation_errors: string[]
  extraction_summary: Record<string, unknown>
  error_detail?: string | null
  theme?: string | null // respondent theme slug (T24); null when unset
  // Detected format profile (slug/label/confidence + the signals that fired);
  // null for authored questionnaires or before extraction ran.
  profile?: {
    slug: string
    label: string
    confidence: number
    signals?: Record<string, unknown>
  } | null
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
  randomize_options?: boolean | null
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
  randomize_options?: boolean
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

  // Survey-level presentation settings (T24). `theme` null clears it to default.
  settings: async (studyId: string, theme: string | null) =>
    api.patch<QuestionnaireStatus>(`/studies/${studyId}/questionnaire/settings`, { theme }),
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
  // Optional segment theme slug (e.g. "fmcg") from the pinned survey metadata,
  // or null. Client-side presentation hint only — maps to an accent palette.
  theme?: string | null
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

  // `theme` (T24) is stamped into this link's pinned snapshot only.
  createLink: async (studyId: string, redirects?: FieldingRedirects, theme?: string | null) =>
    api.post<FieldingLink>(`/studies/${studyId}/fielding/links`, {
      ...(redirects ?? {}),
      ...(theme ? { theme } : {}),
    }),

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

// --- Correction loop (founders teach the questionnaire parser) ---

export interface CorrectionSuggestion {
  source: 'flagged' | 'learned' | 'common'
  text: string
  flag: string | null
}

export interface SuggestionsResponse {
  study_id: string
  question_id: string | null
  profile_slug: string | null
  suggestions: CorrectionSuggestion[]
}

export interface CorrectionResult {
  id: string
  study_id: string
  question_id: string | null
  instruction: string
  scope: string
  profile_slug: string | null
  status: string
  lane: 'live' | 'proposal'
  rule_text: string
  rationale: string
  degraded: boolean
  created_at: string | null
}

export interface PatternKind {
  kind: string
  label: string
  help: string
  values: string[]
}

export interface ProfileRule {
  id: string
  profile_slug: string
  rule_text: string
  /** Set when this rule is machine-applicable — the parsers read it directly. */
  pattern_kind?: string | null
  pattern?: string | null
  pattern_value?: string | null
  scope: string
  ordinal: number
  active: boolean
  approved_by: string | null
  approved_at: string | null
  created_at: string | null
}

export interface Discrepancy {
  qid: string
  kind: string
  severity: string
  detail: string
  n_rows: number
  variables: string[]
  example_rows: number[]
}

export interface CrosscheckReport {
  passed: boolean
  n_rows: number
  checked: Record<string, number>
  skipped: string[]
  findings: Discrepancy[]
  linked_questions: number
  unmatched_questions: string[]
  /** Columns that SHOULD map to a question and do not — hidden variables,
      MaxDiff design and system columns are excluded, being correctly absent. */
  unmatched_variables: string[]
  variable_roles?: Array<{ role: string; label: string; n: number }>
  hidden_variables?: string[]
  /** Conditions audited against the data map before any row was read. */
  conditions?: {
    passed: boolean
    n_checked: number
    issues: Array<{ qid: string; where: string; kind: string; detail: string; severity: string }>
  }
  /** Links made by matching question TEXT because the names differed — a
      judgement a human should confirm. */
  needs_confirmation?: Array<{
    qid: string
    variables: string[]
    confidence: number
    question_text: string
    variable_label: string
  }>
  /** Two-way agreement between the questionnaire and the data map. */
  reconciliation?: {
    in_both: number
    only_in_datamap: number
    only_in_questionnaire: number
    recovered_from_datamap: string[]
  }
  run_by?: string
  run_at?: string
}

export const correctionsApi = {
  suggestions: async (studyId: string, questionId?: string) =>
    api.get<SuggestionsResponse>(`/studies/${studyId}/corrections/suggestions`, {
      params: questionId ? { question_id: questionId } : undefined,
    }),

  create: async (
    studyId: string,
    body: { instruction: string; question_id?: string; scope?: string; suggested_from?: string },
  ) => api.post<CorrectionResult>(`/studies/${studyId}/corrections`, body),

  list: async (studyId: string) =>
    api.get<{ study_id: string; corrections: Record<string, unknown>[] }>(
      `/studies/${studyId}/corrections`,
    ),

  rules: async (profileSlug: string) =>
    api.get<{ profile_slug: string; rules: ProfileRule[] }>(`/profiles/${profileSlug}/rules`),

  applyRule: async (
    profileSlug: string,
    body: {
      rule_text?: string
      scope?: string
      correction_id?: string
      // The machine-applicable half. Supplying these lets the deterministic
      // parsers apply the convention, so it costs nothing on later documents;
      // the server writes the prose half from them.
      pattern_kind?: string
      pattern?: string
      pattern_value?: string
    },
  ) => api.post<ProfileRule>(`/profiles/${profileSlug}/rules`, body),

  patternKinds: async () =>
    api.get<{ kinds: PatternKind[]; all_kinds: string[] }>('/profiles/pattern-kinds'),

  toggleRule: async (profileSlug: string, ruleId: string) =>
    api.post<ProfileRule>(`/profiles/${profileSlug}/rules/${ruleId}/toggle`),
}

export interface FieldChange {
  field: string
  verdict: 'improved' | 'regressed' | 'changed'
  before: unknown
  after: unknown
}

export interface QuestionDiff {
  qid: string
  verdict: 'improved' | 'regressed' | 'changed' | 'unchanged'
  only_in: 'before' | 'after' | null
  flags_cleared: string[]
  flags_added: string[]
  changes: FieldChange[]
}

export interface ExtractionDiff {
  counts: { improved: number; regressed: number; changed: number; unchanged: number }
  n_questions: number
  questions: QuestionDiff[]
}

export interface PreviewState {
  preview_id: string
  status: 'running' | 'ready' | 'error'
  error_detail: string | null
  model_calls: number
  diff: ExtractionDiff | null
}

export const previewApi = {
  start: async (studyId: string, correctionId: string) =>
    api.post<{ preview_id: string; status: string; estimated_model_calls?: number }>(
      `/studies/${studyId}/corrections/${correctionId}/preview`,
    ),

  get: async (studyId: string, correctionId: string) =>
    api.get<PreviewState>(`/studies/${studyId}/corrections/${correctionId}/preview`),

  revise: async (studyId: string, correctionId: string, note: string) =>
    api.post<CorrectionResult>(
      `/studies/${studyId}/corrections/${correctionId}/revise`,
      { note },
    ),

  approve: async (studyId: string, correctionId: string) =>
    api.post<{ rule: ProfileRule; applied: boolean }>(
      `/studies/${studyId}/corrections/${correctionId}/preview/approve`,
    ),
}

// --- Founder-editable UI copy (Tier A) ---

export interface UIKey {
  key: string
  default: string
  where: string
  note: string
  current: string
  overridden: boolean
}

export const uiApi = {
  /** Live overrides for the whole app. An empty map is a valid, normal answer. */
  overrides: async () => api.get<{ overrides: Record<string, string> }>('/ui/overrides'),

  /** The editable-string registry, with defaults and current values. */
  keys: async () => api.get<{ keys: UIKey[]; count: number }>('/ui/keys'),

  set: async (key: string, value: string, note?: string) =>
    api.post<{ key: string; value: string; default: string; overridden: boolean }>(
      '/ui/overrides',
      { key, value, note },
    ),

  revert: async (key: string) =>
    api.post<{ key: string; value: string; overridden: boolean }>(
      `/ui/overrides/${encodeURIComponent(key)}/revert`,
    ),
}

export interface ActivityEntry {
  action: string
  surface: string
  detail: string | null
  actor_id: string | null
  study_id: string | null
  at: string
}

export interface AuditEntry {
  id: string
  entity: string
  entity_id: string
  action: string
  actor_id: string
  study_id: string | null
  summary: string | null
  at: string
}

export const activityApi = {
  /** Fire-and-forget intent telemetry. Never awaited by anything user-visible. */
  record: (body: {
    action: string
    surface?: string
    detail?: string
    study_id?: string
    found?: number
  }) => api.post('/ui/activity', body).catch(() => undefined),

  list: async (surface?: string) =>
    api.get<{
      entries: ActivityEntry[]
      repeated_searches: { query: string; times: number }[]
      count: number
    }>('/ui/activity', { params: surface ? { surface } : undefined }),

  audit: async (studyId?: string) =>
    api.get<{ entries: AuditEntry[]; count: number }>('/corrections/audit', {
      params: studyId ? { study_id: studyId } : undefined,
    }),
}
