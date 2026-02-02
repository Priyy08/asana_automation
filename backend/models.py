from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class TaskModel(BaseModel):
    id: str # Unique ID (e.g. Row Index)
    name: str
    duration: int = 1
    triggering_tasks: List[str] = []
    lag_days: List[int] = []
    section: Optional[str] = None

class ScheduleRequest(BaseModel):
    tasks: List[TaskModel]

class AsanaConfig(BaseModel):
    pat: str
    project_gid: str

class ScheduledTask(BaseModel):
    id: str # Unique ID
    name: str
    start_date: str
    end_date: str
    duration: int
    dependencies: List[str] # List of Predecessor IDs
    dependency_names: List[str] = [] # For display/debug
    section: Optional[str] = None

class SyncRequest(BaseModel):
    config: AsanaConfig
    tasks: List[ScheduledTask]

class DateUpdateRequest(BaseModel):
    config: AsanaConfig
    task_gid: str
    new_end_date: str
