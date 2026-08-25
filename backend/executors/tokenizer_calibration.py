"""Worker-local, provider/model-specific CJK estimator calibration."""
from __future__ import annotations

import logging
import json
import os
import fcntl
from pathlib import Path

from executors import compact_tokens

log = logging.getLogger(__name__)
_reports: dict[str, dict[str, float | int]] = {}

_SAMPLES = (
    "这是中文校准样本，用于估算上下文窗口。",
    "Mixed 中文 and English: function({\"status\": \"ok\"})",
    '{"tool_result":"文件已写入","path":"src/配置.json"}',
)


def load_configured_tokenizers(paths: dict[str, str]) -> dict[str, dict[str, float | int]]:
    """Load only explicit local files; a bad optional tokenizer is fail-soft."""
    calibration_path = Path(os.environ.get("NUKE_TOKENIZER_CALIBRATION_PATH") or "./tokenizer_calibration.json")
    try:
        for key, report in json.loads(calibration_path.read_text(encoding="utf-8")).items():
            if isinstance(report, dict) and "adjustment" in report:
                _reports[key] = report
                compact_tokens.register_cjk_calibration(key, report["adjustment"])
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    for key, raw_path in paths.items():
        if not isinstance(key, str) or not isinstance(raw_path, str):
            continue
        try:
            from tokenizers import Tokenizer
            path = Path(raw_path)
            if not path.is_file():
                raise FileNotFoundError(path)
            report = compact_tokens.calibrate_cjk_estimator(_SAMPLES, Tokenizer.from_file(str(path)))
            _reports[key] = report
            compact_tokens.register_cjk_calibration(key, report["adjustment"])
            _persist_reports(calibration_path, key, report)
            log.info("tokenizer calibrated for %s: samples=%s mae=%.2f", key, report["samples"], report["mean_abs_error"])
        except Exception:
            log.exception("tokenizer calibration skipped for %s", key)
    return dict(_reports)


def _persist_reports(path: Path, key: str, report: dict[str, float | int]) -> None:
    """Merge one report under an inter-process advisory lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                stored = {}
            if not isinstance(stored, dict):
                stored = {}
            stored[str(key)] = report
            tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(stored, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def reports() -> dict[str, dict[str, float | int]]:
    return dict(_reports)


def activate(provider: str, model: str) -> float:
    return compact_tokens.activate_cjk_calibration(f"{provider}/{model}")
