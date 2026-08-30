"""Pydantic models for API requests/responses."""

from pydantic import BaseModel, EmailStr


class StudyMetadata(BaseModel):
    """Study metadata."""

    classification_confidence: float | None = None
    qc_passed: bool | None = None
    qc_issues: list[str] = []
    qc_warnings: list[str] = []
    error: str | None = None


class ApprovalRecord(BaseModel):
    """One approval in the chain."""

    gate: str
    approved_by: str
    approved_at: str


class StudyResponse(BaseModel):
    """Study detail response."""

    id: str
    client_name: str
    status: str
    created_at: str
    updated_at: str
    metadata: StudyMetadata
    approval_chain: list[ApprovalRecord]
    excel_path: str | None = None
    pptx_path: str | None = None

    class Config:
        from_attributes = True


class StudyListResponse(BaseModel):
    """List of studies."""

    studies: list[StudyResponse]
    total: int
    page: int
    limit: int


class UploadResponse(BaseModel):
    """Upload file response."""

    study_id: str
    status: str
    message: str


class CreateStudyRequest(BaseModel):
    """Create a data-less study (survey-authoring path — no file, no pipeline).

    `kind` is the frontend's chooser selection ("questionnaire" | "builder");
    it's recorded in metadata for provenance, not used for routing (the client
    navigates itself). `client_name` is the project name.
    """

    client_name: str = ""
    kind: str | None = None


class ApprovalRequest(BaseModel):
    """Approval request body."""

    approved_by: str = "manual"
    notes: str | None = None


class DownloadResponse(BaseModel):
    """Download response."""

    download_url: str
    filename: str
    format: str  # "excel", "pptx", or "both"


# Auth models
class UserResponse(BaseModel):
    """User response."""

    id: str
    email: str
    name: str
    role: str
    created_at: str
    is_active: bool

    class Config:
        from_attributes = True


class LoginResponse(BaseModel):
    """Login response."""

    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class CreateUserRequest(BaseModel):
    """Create user request."""

    email: EmailStr
    name: str
    role: str = "analyst"
    password: str


class EmergencyResetRequest(BaseModel):
    """Emergency admin password reset request — see ADMIN_RESET_TOKEN in
    api/config.py. Unauthenticated by design (that's the point: recovering
    a locked-out owner account), gated by the token instead of a session.
    """

    email: EmailStr
    token: str
    new_password: str


class ShareLinkResponse(BaseModel):
    """Share link response."""

    token: str
    study_id: str
    created_by: str
    created_at: str
    revoked: bool

    class Config:
        from_attributes = True


class PublicStudyResponse(BaseModel):
    """Public study response (thin payload for share links)."""

    client_name: str
    status: str
    created_at: str
    excel_available: bool
    pptx_available: bool


# Chat models
class ChatToolCall(BaseModel):
    """A tool call from the AI assistant."""

    tool: str  # query_data, show_chart, compare_variables, navigate_to
    params: dict


class ChatMessage(BaseModel):
    """One chat message in the conversation."""

    id: str
    role: str  # user or assistant
    content: str
    tools: list[ChatToolCall] | None = None
    created_at: str


class ChatRequest(BaseModel):
    """User chat request."""

    message: str


class ChatResponse(BaseModel):
    """Assistant chat response."""

    message: ChatMessage
    tool_results: list[dict] | None = None
    conversation_id: str


# Questionnaire models
class QuestionnaireUploadResponse(BaseModel):
    """Response to a questionnaire upload — returns immediately while the
    extraction pipeline runs in the background."""

    questionnaire_id: str
    study_id: str
    status: str
    message: str


class QuestionnaireStatusResponse(BaseModel):
    """Questionnaire status + extraction summary for the review dashboard."""

    id: str
    study_id: str
    status: str
    source_filename: str
    version: int
    created_at: str
    updated_at: str
    title: str | None = None
    n_questions: int = 0
    n_approved: int = 0
    # "extracted" or "authored" — lets the builder vs. review UIs decide which one
    # owns a given questionnaire (the builder redirects extracted docs to review).
    source: str | None = None
    validation_errors: list[str] = []
    extraction_summary: dict = {}
    # Set only when status == "error": the human-readable failure reason (e.g. a
    # missing ANTHROPIC_API_KEY surfaced by the extractor circuit breaker). None
    # otherwise, so a healthy questionnaire carries no stale error text.
    error_detail: str | None = None
    # Respondent theme slug from metadata (T24); None when unset. Lets the builder
    # show the currently-selected theme.
    theme: str | None = None
    # Detected questionnaire format profile: {slug, label, confidence, signals}.
    # Set once extraction ran; None for authored/blank questionnaires. Lets the
    # review UI show which client convention was recognized.
    profile: dict | None = None


class QuestionnaireSettingsRequest(BaseModel):
    """Survey-level presentation settings (T24). Currently only the respondent
    theme slug; validated against THEME_SLUGS by the route. Explicit null clears
    it back to the default."""

    theme: str | None = None


class QuestionUpdateRequest(BaseModel):
    """Partial update to one extracted question. Any omitted field is left
    unchanged; provided fields overwrite. Routing fields accept the same nested
    dict shape Expression.to_dict()/from_dict() use (or null to clear)."""

    # A qid rename is the only way to repair a duplicate-qid extraction (T18).
    # When present and different from the current qid, the route rewrites the
    # block membership, approved-qids set, and any other question's routing that
    # referenced the old qid.
    qid: str | None = None
    text: str | None = None
    type: str | None = None
    options: list[dict] | None = None
    # rows/cols/required added for the authoring builder (T12): a grid_single
    # needs its rows editable, and an authored question needs its required flag
    # toggled. Extracted-review callers simply omit them (left unchanged).
    rows: list[dict] | None = None
    cols: list[dict] | None = None
    required: bool | None = None
    randomize_options: bool | None = None
    show_if: dict | None = None
    terminate_if: dict | None = None

    # "Clear this field" (explicit null) vs. "leave unchanged" (omitted) are
    # distinguished by the route via `model_fields_set`, since both surface here
    # as None.


class QuestionnaireCreateRequest(BaseModel):
    """Create a blank authored questionnaire from scratch (T12 builder)."""

    title: str = ""


class QuestionCreateRequest(BaseModel):
    """Append one new question to an authored questionnaire (T12 builder).

    Same field shapes as QuestionUpdateRequest, but qid/type are required (a new
    question needs an identity). Routing dicts use Expression.to_dict() shape."""

    qid: str
    text: str = ""
    type: str = "single"
    options: list[dict] = []
    rows: list[dict] | None = None
    cols: list[dict] | None = None
    required: bool = True
    randomize_options: bool = False
    show_if: dict | None = None
    terminate_if: dict | None = None


class QuestionOrderRequest(BaseModel):
    """Reorder a questionnaire's questions (T12 builder). `qids` must be a
    permutation of every existing qid — full list, no adds/removes."""

    qids: list[str]


# --- Fielding models (T9') --------------------------------------------------


class FieldingLinkResponse(BaseModel):
    """A created/listed fielding link. `respondent_path` is the public URL path
    a respondent visits (no auth); `counts` is present in list responses."""

    id: str
    study_id: str
    questionnaire_id: str
    token: str
    status: str  # open | closed
    survey_version: int
    created_by: str
    created_at: str
    closed_at: str | None = None
    respondent_path: str
    counts: dict = {}
    # Vendor redirect templates (T10); None when unset.
    redirect_complete: str | None = None
    redirect_terminate: str | None = None
    redirect_overquota: str | None = None
    redirect_security: str | None = None


class FieldingLinkListResponse(BaseModel):
    """All fielding links for a study, each with per-link session counts."""

    study_id: str
    links: list[FieldingLinkResponse] = []


class RespondentEntryResponse(BaseModel):
    """Payload served to a respondent on entry: a fresh session id plus the
    compiled SurveyJS JSON (rendering only — no terminate logic is embedded;
    the server enforces `terminate_if`)."""

    session_id: str
    status: str = "open"  # open | closed
    surveyjs_json: dict = {}
    current_page: int = 0
    # Optional segment theme slug from the pinned survey's metadata (e.g. "fmcg"),
    # or None. Purely a client-side presentation hint for accent palettes.
    theme: str | None = None
    # Set only on a terminal-at-entry outcome (T10 duplicate-pid security = 30).
    # `redirect_url` is the substituted vendor URL, or None to use built-in screens.
    status_code: int | None = None
    redirect_url: str | None = None


class RespondentAnswersRequest(BaseModel):
    """A page's worth of answers plus the page the respondent is now on."""

    answers: dict = {}
    current_page: int = 0


class RespondentSessionResponse(BaseModel):
    """Resume payload: accumulated answers + progress + any terminal status."""

    session_id: str
    status_code: int | None = None
    current_page: int = 0
    answers: dict = {}
    completed_at: str | None = None
    # Vendor redirect for a resumed terminal session (T10); None otherwise.
    redirect_url: str | None = None


# --- Quota + redirect models (T10) ------------------------------------------


class QuotaCellCreate(BaseModel):
    """One quota cell to create. `condition` is Expression.to_dict() shape."""

    name: str
    condition: dict
    limit: int


class QuotaCellsCreateRequest(BaseModel):
    """Create a batch of cells on a link."""

    cells: list[QuotaCellCreate]


class QuotaCellResponse(BaseModel):
    """A quota cell with its live count."""

    id: str
    fielding_link_id: str
    name: str
    condition: dict
    limit: int
    count: int


class QuotaCellsResponse(BaseModel):
    """All quota cells for a link."""

    link_id: str
    cells: list[QuotaCellResponse] = []


class QuotaCellUpdateRequest(BaseModel):
    """PATCH a cell. `limit` raises/lowers the target; `condition` is rejected
    once the link has started fielding (changing the definition mid-field would
    invalidate already-counted completes)."""

    limit: int | None = None
    condition: dict | None = None


class FieldingRedirectsRequest(BaseModel):
    """Vendor redirect templates — settable at link creation and via PATCH.
    Omitted fields are left unchanged; an explicit null clears a template."""

    redirect_complete: str | None = None
    redirect_terminate: str | None = None
    redirect_overquota: str | None = None
    redirect_security: str | None = None


class FieldingLinkCreateRequest(FieldingRedirectsRequest):
    """Create-a-link body: redirect templates plus an optional respondent `theme`
    slug (T24). The theme is stamped into the link's PINNED survey snapshot only;
    the questionnaire's own definition is left untouched (a link-level
    presentation choice, not an instrument edit)."""

    theme: str | None = None


# --- Correction loop (founders teach the questionnaire parser) ---


class CorrectionRequest(BaseModel):
    """A founder's plain-language correction against an extracted questionnaire.

    `question_id` is optional because a correction anchored on one question is
    very often a statement about the whole document. `scope` is what the author
    CONFIRMED in the UI (the chip); when omitted the model's inference is used.
    """

    instruction: str
    question_id: str | None = None
    scope: str | None = None
    suggested_from: str | None = None


class CorrectionResponse(BaseModel):
    """A stored correction plus the rule drafted from it. Nothing is applied yet."""

    id: str
    study_id: str
    question_id: str | None = None
    instruction: str
    scope: str
    profile_slug: str | None = None
    status: str
    lane: str
    rule_text: str
    rationale: str
    degraded: bool = False
    created_at: str | None = None


class SuggestionResponse(BaseModel):
    """Ranked opening sentences for the instruction box (flagged/learned/common)."""

    study_id: str
    question_id: str | None = None
    profile_slug: str | None = None
    suggestions: list[dict]


class ApplyRuleRequest(BaseModel):
    """Approve a drafted rule onto a format profile. `scope` 'global' needs owner."""

    rule_text: str
    scope: str | None = None
    correction_id: str | None = None


class ProfileRuleResponse(BaseModel):
    """One learned rule in a profile's registry."""

    id: str
    profile_slug: str
    rule_text: str
    scope: str
    ordinal: int
    active: bool
    approved_by: str | None = None
    approved_at: str | None = None
    created_at: str | None = None


class ReviseRequest(BaseModel):
    """Send a correction back to the model with what was wrong about its attempt."""

    note: str


class ReorderRulesRequest(BaseModel):
    """New order for a profile's rules. Later rules win on conflict."""

    rule_ids: list[str]


class UIOverrideRequest(BaseModel):
    """Change one piece of UI copy. `key` must be in the UI_KEYS registry."""

    key: str
    value: str
    note: str | None = None


class ActivityRequest(BaseModel):
    """A founder's search or view. Intent telemetry, never load-bearing."""

    action: str  # searched | viewed | opened | abandoned
    surface: str | None = None  # wording | corrections | questionnaire
    detail: str | None = None  # the query, or what was viewed
    study_id: str | None = None
    found: int | None = None  # result count, so "searched and found nothing" is visible


class SaveCodeframeRequest(BaseModel):
    """Add an approved codeframe to the reuse library."""

    question_text: str
    codeframe: dict
    study_id: str | None = None
    client_name: str | None = None
