from datetime import datetime

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    source_paths: list[str]


class BindingTaskCreateRequest(BaseModel):
    binding_ids: list[int] = Field(default_factory=list)


class TaskResponse(BaseModel):
    id: int
    owner_wallet_address: str
    kb_id: int
    task_type: str
    status: str
    source_paths: list[str]
    stats_json: dict
    error_message: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_state: str | None = None
    queue_position: int | None = None
    current_running_task_id: int | None = None
    current_running_task_type: str | None = None
    cancelable: bool = False

    model_config = {"from_attributes": True}
