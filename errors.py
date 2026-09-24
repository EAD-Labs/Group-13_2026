"""One error model for the whole project.

Every failure the API can report is an AppError subclass. api.py turns them
into a single JSON envelope:

    {"error": {"code", "message", "details": [{"field", "message"}], "trace_id"}}

`message` is always safe to show a teacher. Internal detail goes to the log.
"""


class AppError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        # [{"field": "content.topic", "message": "..."}]
        self.details = details or []


class InputValidationError(AppError):
    code = "invalid_input"
    http_status = 422


class IncompleteConstraintsError(AppError):
    code = "incomplete_constraints"
    http_status = 422


class UnsafeTopicError(AppError):
    code = "unsafe_topic"
    http_status = 422


class OffTopicError(AppError):
    code = "topic_not_in_source"
    http_status = 422


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class RetrievalUnavailableError(AppError):
    code = "index_unavailable"
    http_status = 503


class LLMError(AppError):
    code = "llm_failure"
    http_status = 502


class AgentOutputError(AppError):
    code = "agent_output_invalid"
    http_status = 502
