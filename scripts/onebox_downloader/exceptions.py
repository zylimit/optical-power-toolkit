"""Exception hierarchy: fatal (need re-login) vs recoverable (skip one file)."""


class OneboxDownloaderError(Exception):
    """Base for all downloader errors."""


class AuthExpiredError(OneboxDownloaderError):
    """Session/cookies missing or expired -- stop and prompt --login."""


class ApiRequestError(OneboxDownloaderError):
    """A single API/network call failed -- usually recoverable per-file."""
