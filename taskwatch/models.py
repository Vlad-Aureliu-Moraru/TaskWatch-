from dataclasses import dataclass


@dataclass
class Archive:
    id: int
    name: str


@dataclass
class Directory:
    id: int
    archive_id: int
    name: str


@dataclass
class Task:
    id: int
    directory_id: int
    name: str
    description: str = ""
    time_dedicated: int = 0
    repeatable: bool = False
    finished: bool = False
    deadline: str = "none"
    urgency: int = 1
    repeatable_type: str = "none"
    difficulty: int = 1
    finished_date: str = "none"
    has_to_be_completed_to_repeat: bool = True
    repeat_on_specific_day: str = "none"
    position: int = 0


@dataclass
class Note:
    id: int
    task_id: int
    date: str
    note: str


@dataclass
class Tag:
    id: int
    name: str
