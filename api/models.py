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
    validation_errors: list[str] = []
    extraction_summary: dict = {}


class QuestionUpdateRequest(BaseModel):
    """Partial update to one extracted question. Any omitted field is left
    unchanged; provided fields overwrite. Routing fields accept the same nested
    dict shape Expression.to_dict()/from_dict() use (or null to clear)."""

    text: str | None = None
    type: str | None = None
    options: list[dict] | None = None
    show_if: dict | None = None
    terminate_if: dict | None = None

    # "Clear this field" (explicit null) vs. "leave unchanged" (omitted) are
    # distinguished by the route via `model_fields_set`, since both surface here
    # as None.
