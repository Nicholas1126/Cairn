from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Settings(BaseModel):
    intent_timeout: int = Field(ge=5)
    reason_timeout: int = Field(ge=5)
    execution_record_enabled: bool = True
    execution_file_logging: bool = True


class ExecutionReport(BaseModel):
    phase: str
    intent_id: str | None = None
    worker_name: str
    model: str | None = None
    command: list[str]
    prompt: str
    response_text: str | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    outcome: str
    started_at: str
    ended_at: str
    duration_ms: int = 0
    produced_fact_id: str | None = None
    produced_intent_ids: list[str] = Field(default_factory=list)


class ExecutionSummary(BaseModel):
    id: str
    phase: str
    intent_id: str | None = None
    worker_name: str
    model: str | None = None
    outcome: str
    exit_code: int | None = None
    started_at: str
    ended_at: str
    duration_ms: int = 0
    produced_fact_id: str | None = None
    produced_intent_ids: list[str] = Field(default_factory=list)
    has_log: bool = False
    command_preview: str = ""  # short, redacted preview of the executed command for the Runtime list


class ExecutionDetail(ExecutionSummary):
    command: list[str]
    prompt: str
    response_text: str | None = None
    stdout_inline: str | None = None
    stderr_inline: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    truncated: bool = False
    log_path: str | None = None


class ChatWorker(BaseModel):
    name: str
    type: str
    model: str | None = None


class ChatTurnRequest(BaseModel):
    worker: str
    message: str
    session: str | None = None
    debug: bool = False


class ChatTurnResult(BaseModel):
    reply: str
    session: str | None = None
    command: list[str]
    prompt: str
    stdout: str
    exit_code: int | None = None
    outcome: str
    duration_ms: int = 0


class EngineOverride(BaseModel):
    path: str
    launcher: Literal["direct", "cmd", "powershell"] = "direct"


class EngineInfo(BaseModel):
    type: str
    binary: str
    launchable: bool
    path: str | None = None
    version: str | None = None
    source: str | None = None
    override: EngineOverride | None = None


class Fact(BaseModel):
    id: str
    description: str


class Intent(BaseModel):
    id: str
    from_: list[str] = Field(alias="from")
    to: str | None = None
    description: str
    creator: str
    worker: str | None = None
    last_heartbeat_at: str | None = None
    created_at: str
    concluded_at: str | None = None

    model_config = {"populate_by_name": True}


class Hint(BaseModel):
    id: str
    content: str
    creator: str
    created_at: str


class ProjectReason(BaseModel):
    worker: str
    trigger: str
    started_at: str
    last_heartbeat_at: str


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: Literal["active", "stopped", "completed"]
    bootstrap_enabled: bool
    backend: Literal["docker", "local"] = "docker"
    created_at: str
    reason: ProjectReason | None = None


class ProjectSummary(ProjectMeta):
    fact_count: int
    intent_count: int
    working_intent_count: int
    unclaimed_intent_count: int
    hint_count: int


class ProjectDetail(BaseModel):
    project: ProjectMeta
    facts: list[Fact]
    intents: list[Intent]
    hints: list[Hint]


class CreateHintInline(BaseModel):
    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateProjectRequest(BaseModel):
    title: str
    origin: str
    goal: str
    bootstrap_enabled: bool = True
    backend: Literal["docker", "local"] = "docker"
    hints: list[CreateHintInline] | None = None

    @field_validator("title", "origin", "goal")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateHintRequest(BaseModel):
    content: str
    creator: str

    @field_validator("content", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CreateIntentRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    creator: str
    worker: str | None = None

    model_config = {"populate_by_name": True}

    @field_validator("description", "creator", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        return cleaned


class HeartbeatRequest(BaseModel):
    worker: str

    @field_validator("worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReasonClaimRequest(BaseModel):
    worker: str
    trigger: str

    @field_validator("worker", "trigger")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ConcludeRequest(BaseModel):
    worker: str
    description: str

    @field_validator("worker", "description")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class CompleteRequest(BaseModel):
    from_: list[str] = Field(alias="from", min_length=1)
    description: str
    worker: str

    model_config = {"populate_by_name": True}

    @field_validator("description", "worker")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text

    @field_validator("from_")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        cleaned = []
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("fact ids must not be empty")
            cleaned.append(text)
        return cleaned


class ConcludeResponse(BaseModel):
    fact: Fact
    intent: Intent


class UpdateProjectStatusRequest(BaseModel):
    status: Literal["active", "stopped"]


class UpdateProjectTitleRequest(BaseModel):
    title: str

    @field_validator("title")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenRequest(BaseModel):
    description: str
    creator: str

    @field_validator("description", "creator")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("must not be empty")
        return text


class ReopenResponse(BaseModel):
    project: ProjectMeta
    fact: Fact
    intent: Intent


class SkillInfo(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True


class SkillContent(BaseModel):
    name: str
    content: str


class SkillCreate(BaseModel):
    name: str
    content: str


class SkillEnable(BaseModel):
    enabled: bool
