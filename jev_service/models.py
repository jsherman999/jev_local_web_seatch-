from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Verification(BaseModel):
    """Optional literal checks on the final rendered document; all must pass."""

    model_config = ConfigDict(extra="forbid")
    url_contains: str | None = Field(default=None, min_length=1, max_length=500)
    text_contains: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("text_contains")
    @classmethod
    def valid_checks(cls, values):
        if any(not x.strip() or len(x) > 500 for x in values):
            raise ValueError("Each text check must contain 1–500 nonblank characters")
        return values


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: HttpUrl = Field(description="Starting HTTP(S) URL. No file or javascript URLs.")
    goal: str = Field(min_length=1, max_length=8000, description="One natural-language browser task.")
    max_steps: int = Field(default=25, ge=1, le=50)
    timeout_seconds: int = Field(
        default=120, ge=5, le=600, description="Execution limit, excluding queue time."
    )
    max_text_chars: int = Field(default=30000, ge=100, le=100000)
    capture_screenshots: bool = Field(default=False, description="Capture browser previews after decisions.")
    isolate_browser: bool = Field(default=False, description="Use a fresh browser context for this job.")
    verify: Verification = Field(default_factory=Verification)

    @field_validator("goal")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Goal cannot be blank")
        return value.strip()

    @field_validator("url")
    @classmethod
    def no_credentials(cls, value):
        if value.username or value.password:
            raise ValueError("Do not put credentials in URLs")
        return value


class SourceLink(BaseModel):
    text: str
    url: str


class PageResult(BaseModel):
    url: str
    title: str
    text: str
    text_truncated: bool
    links: list[SourceLink]
    links_truncated: bool = False
    captured_at: str


class BrowserResult(BaseModel):
    agent_status: str
    verification: Literal["passed", "failed", "not_requested"]
    checks: list[dict]
    page: PageResult
    visited_urls: list[str]
    steps: int
    elapsed_ms: int
    actions: list[dict]
    usage: dict = Field(default_factory=dict)


Status = Literal[
    "queued", "running", "completed", "blocked", "failed", "timed_out", "cancelled", "interrupted"
]


class Job(BaseModel):
    id: str
    status: Status
    created_at: str
    updated_at: str
    request: JobRequest
    progress: dict = Field(default_factory=dict)
    result: BrowserResult | None = None
    error: str | None = None


TERMINAL = {"completed", "blocked", "failed", "timed_out", "cancelled", "interrupted"}
