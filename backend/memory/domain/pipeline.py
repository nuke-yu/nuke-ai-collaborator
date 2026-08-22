"""Pure identity rules for durable Memory pipeline jobs."""
from __future__ import annotations

import hashlib


def pipeline_job_identity(
    job_type: str,
    group_id: int,
    input_id: str,
    input_version: str,
) -> tuple[str, str]:
    key = f"{job_type}:{group_id}:{input_id}:{input_version}"
    return "job:" + hashlib.sha256(key.encode()).hexdigest()[:24], key
