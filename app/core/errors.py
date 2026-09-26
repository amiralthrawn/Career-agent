"""Domain errors raised by services; translated to HTTP responses in `app.main`."""


class DomainError(Exception):
    """Base class for expected business errors."""


class NotFoundError(DomainError):
    pass


class ConflictError(DomainError):
    pass


class UnprocessableError(DomainError):
    """The input is well-formed but cannot be processed (e.g. an invalid document).

    Messages must be generic: they must never contain document content.
    """
