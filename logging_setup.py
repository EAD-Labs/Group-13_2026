"""Structured interaction logging.

Every event is one JSON line in logs/interactions.jsonl carrying the request's
trace_id and a seq number that increases within that trace, so the full order
of human->agent and agent->agent interactions can be rebuilt afterwards (see
trace.py).

Each trace (one lesson: the plan, then its handouts/quiz and citation clicks,
which reuse the trace id) also gets its own readable session file,
logs/sessions/<started>_<trace_id>.log. It shows the same events plus their
`detail` payloads: what each agent was given, what it returned, and what it
handed to the next agent.

Actors: human, api, validator, retriever, agent:ck, agent:pk, agent:tk,
agent:tpack, agent:materials.
"""

import contextvars
import glob
import json
import logging
import os
import sys
import threading
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
        if config.LOG_FULL_PAYLOADS:
            entry.update(getattr(record, "event_detail", None) or {})
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


class SessionFileHandler(logging.Handler):
    """Append each traced event to that trace's own readable log file."""

    def __init__(self, directory):
        super().__init__()
        self.directory = directory
        self._paths = {}
        self._paths_lock = threading.Lock()

    def path_for(self, trace_id, create=True):
        with self._paths_lock:
            path = self._paths.get(trace_id)
            if path is None:
                # A later request of the same trace (or a server restart) reuses the file.
                existing = glob.glob(os.path.join(self.directory, f"*_{trace_id}.log"))
                if existing:
                    path = existing[0]
                elif not create:
                    return None
                else:
                    os.makedirs(self.directory, exist_ok=True)
                    self._prune()
                    started = datetime.now().strftime("%Y%m%d-%H%M%S")
                    path = os.path.join(self.directory, f"{started}_{trace_id}.log")
                if len(self._paths) > 1000:
                    self._paths.clear()
                self._paths[trace_id] = path
            return path

    def _prune(self):
        files = sorted(glob.glob(os.path.join(self.directory, "*.log")))
        for old in files[: max(0, len(files) - config.LOG_SESSION_MAX_FILES + 1)]:
            try:
                os.remove(old)
            except OSError:
                pass

    def emit(self, record):
        fields = getattr(record, "event_fields", None)
        if not fields or not fields.get("trace_id"):
            return
        try:
            path = self.path_for(fields["trace_id"])
            new_file = not os.path.exists(path)
            text = self.format(record)
            with open(path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write(f"AgentTPACK session log - trace {fields['trace_id']}\n")
                    f.write("Times are local. [seq] restarts at 1 for each HTTP request of the session.\n")
                f.write(text + "\n")
        except Exception:
            self.handleError(record)


class SessionFormatter(logging.Formatter):
    """Readable, multi-line rendering of one event for a session file."""

    _SKIP = {"trace_id", "seq", "actor", "event"}

    def format(self, record):
        fields = record.event_fields
        event = fields.get("event", "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        lines = []
        if event == "human.request":
            lines += ["", "=" * 100, f"{fields.get('method', '')} {fields.get('path', '')}", "=" * 100]

        extras = {k: v for k, v in fields.items() if k not in self._SKIP}
        if event == "agent.handoff":
            summary = f"{extras.pop('from', '?')} --> {extras.pop('to', '?')}  "
        else:
            summary = ""
        if "duration_ms" in extras:
            summary += f"took {_seconds(extras.pop('duration_ms'))}  "
        summary += " ".join(f"{k}={_compact(v)}" for k, v in extras.items())
        level = "" if record.levelno == logging.INFO else f"{record.levelname} "
        lines.append(f"{ts} [{fields.get('seq', '?'):>3}] {level}{fields.get('actor', ''):<16} {event:<22} {summary}".rstrip())

        if config.LOG_SESSION_PAYLOADS:
            for name, value in (getattr(record, "event_detail", None) or {}).items():
                lines.append(f"    | {name}:")
                body = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, default=str)
                lines += [f"    |   {line}" for line in body.splitlines()]
        if record.exc_info:
            lines += ["    | " + line for line in self.formatException(record.exc_info).splitlines()]
        if event == "human.response":
            lines.append("-" * 100)
        return "\n".join(lines)


def _seconds(ms):
    return f"{ms / 1000:.2f}s" if isinstance(ms, (int, float)) else str(ms)


def _compact(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 300 else text[:297] + "..."


_session_handler = None


def session_log_path(trace_id):
    """Path of the session file for `trace_id`, or None if it has none yet."""
    if _session_handler is None or not trace_id:
        return None
    return _session_handler.path_for(trace_id, create=False)


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

    global _session_handler
    if config.LOG_SESSION_FILES:
        _session_handler = SessionFileHandler(os.path.join(config.LOG_DIR, config.LOG_SESSION_DIR))
        _session_handler.setFormatter(SessionFormatter())
        logger.addHandler(_session_handler)

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


def log_event(event, actor, level=logging.INFO, exc_info=None, detail=None, **fields):
    """Write one structured event for the current trace.

    `fields` are short values shown everywhere. `detail` holds large payloads
    (agent inputs and outputs, hand-off contents, prompts): they are written
    to the session file (config.LOG_SESSION_PAYLOADS) and to the JSONL log
    only when config.LOG_FULL_PAYLOADS is on, never to the console.
    """
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
        extra={"event_fields": record_fields, "event_detail": detail},
    )
