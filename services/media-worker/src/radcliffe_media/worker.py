from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkerInfo:
    name: str = "radcliffe-media-worker"


def info() -> WorkerInfo:
    return WorkerInfo()
