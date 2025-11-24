"""Contex SDK exceptions"""


class ContexError(Exception):
    """Base exception for all Contex SDK errors"""
    pass


class AuthenticationError(ContexError):
    """Raised when authentication fails"""
    pass


class RateLimitError(ContexError):
    """Raised when rate limit is exceeded"""
    
    def __init__(self, message: str, retry_after: int = None):
        super().__init__(message)
        self.retry_after = retry_after


class ValidationError(ContexError):
    """Raised when request validation fails"""
    pass


class NotFoundError(ContexError):
    """Raised when resource is not found"""
    pass


class ServerError(ContexError):
    """Raised when server returns 5xx error"""
    pass


class NetworkError(ContexError):
    """Raised when network request fails"""
    pass


class TimeoutError(ContexError):
    """Raised when request times out"""
    pass
