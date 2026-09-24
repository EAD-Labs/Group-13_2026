"""Structured interaction logging.

Every event is one JSON line in logs/interactions.jsonl carrying the request's
trace_id and a seq number that increases within that trace, so the full order
of human->agent and agent->agent interactions can be rebuilt afterwards (see
trace.py).

Actors: human, api, validator, retriever, agent:ck, agent:pk, agent:tk,
agent:tpack.
"""

import contextvars
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import config

LOGGER_NAME = "agenttpack"

_trace_id = contextvars.ContextVar("trace_id", default=None)
_seq = contextvars.ContextVar("seq", default=None)

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
        }
        entry.update(getattr(record, "event_fields", {}) or {"message": record.getMessage()})
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        fields = getattr(record, "event_fields", None)
        if not fields:
            return super().format(record)
        trace = (fields.get("trace_id") or "-")[:8]
        extras = {
            k: v
            for k, v in fields.items()
            if k not in ("trace_id", "seq", "actor", "event")
        }
        tail = " ".join(f"{k}={v}" for k, v in extras.items())
        return f"[{trace} #{fields.get('seq')}] {fields.get('actor')} {fields.get('event')} {tail}".rstrip()


def configure_logging():
    """Attach the JSONL file handler and console handler. Safe to call twice."""
    global _configured
    if _configured:
        return
    os.makedirs(config.LOG_DIR, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    file_handler = RotatingFileHandler(
        os.path.join(config.LOG_DIR, config.LOG_FILE),
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(ConsoleFormatter())
    logger.addHandler(console)

    _configured = True


def start_trace(trace_id=None):
    """Begin a new trace for the current request context. Returns its id."""
    if not trace_id or not _valid_trace_id(trace_id):
        trace_id = uuid.uuid4().hex
    _trace_id.set(trace_id)
    _seq.set([0])
    return trace_id


def current_trace_id():
    return _trace_id.get()


def _valid_trace_id(value):
    return 8 <= len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def _next_seq():
    counter = _seq.get()
    if counter is None:
        counter = [0]
        _seq.set(counter)
    counter[0] += 1
    return counter[0]


def log_event(event, actor, level=logging.INFO, exc_info=None, **fields):
    """Write one structured event for the current trace."""
    record_fields = {
        "trace_id": _trace_id.get(),
        "seq": _next_seq(),
        "actor": actor,
        "event": event,
    }
    record_fields.update(fields)
    logging.getLogger(LOGGER_NAME).log(
        level,
        event,
        exc_info=exc_info,
        extra={"event_fields": record_fields},
    )
