from __future__ import annotations


class SpectralError(Exception):
    pass


class SidecarUnavailableError(SpectralError):
    pass


class LeaseExpiredError(SpectralError):
    pass


class APIError(SpectralError):
    def __init__(self, op: str, status_code: int, message: str) -> None:
        self.op = op
        self.status_code = status_code
        self.message = message
        if message:
            super().__init__(f"spectral {op}: http {status_code}: {message}")
        else:
            super().__init__(f"spectral {op}: http {status_code}")


class InvalidRegistrationError(APIError):
    pass


class AlreadyRegisteredError(APIError):
    pass


class ServiceNotFoundError(APIError):
    pass


class SidecarTimeoutError(APIError):
    pass


def api_error(op: str, status_code: int, message: str) -> SpectralError:
    if status_code == 504:
        return SidecarTimeoutError(op, status_code, message)
    if op == "register" and status_code == 400:
        return InvalidRegistrationError(op, status_code, message)
    if op == "register" and status_code == 409:
        return AlreadyRegisteredError(op, status_code, message)
    if op == "lookup" and status_code == 404:
        return ServiceNotFoundError(op, status_code, message)
    if op == "renew" and status_code == 404:
        cause = APIError(op, status_code, message)
        expired = LeaseExpiredError(str(cause))
        expired.__cause__ = cause
        return expired
    return APIError(op, status_code, message)
