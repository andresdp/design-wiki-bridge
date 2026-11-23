"""Provider errors that no retry can fix: out of credit, a bad or missing key, no access.

The query graph keeps a question going when one source fails (it is answered from the rest,
degraded). That rule must not apply to these errors: an exhausted account would otherwise
turn every remaining question into a failed or degraded trace (a failed question scores 0).
Every catch-all in the pipeline calls ``raise_if_fatal`` first, and the runner stops the run
without saving the question in progress, so ``dwb experiment run --resume`` can finish it
once the account is topped up.

Ordinary rate limits (HTTP 429 without ``insufficient_quota``) are not fatal: the clients
retry them (``MODEL_NET.max_retries``).

The error classes are matched by module and name, so this module imports neither SDK.
"""

from __future__ import annotations

# (module prefix, class name)
_FATAL_CLASSES = {
    ("openai", "AuthenticationError"),
    ("openai", "PermissionDeniedError"),
    ("tavily", "InvalidAPIKeyError"),
    ("tavily", "MissingAPIKeyError"),
    ("tavily", "UsageLimitExceededError"),
    ("tavily", "ForbiddenError"),
}
_QUOTA_MARKERS = ("insufficient_quota", "exceeded your current quota", "billing")


class FatalAPIError(RuntimeError):
    """A provider refused the call for a reason that waiting or retrying cannot fix."""


def _fatal_one(e: BaseException) -> bool:
    for cls in type(e).__mro__:
        mod = cls.__module__.split(".")[0]
        if (mod, cls.__name__) in _FATAL_CLASSES:
            return True
        if mod == "openai" and cls.__name__ == "RateLimitError":
            code = getattr(e, "code", None)
            return code == "insufficient_quota" or any(k in str(e).lower()
                                                       for k in _QUOTA_MARKERS)
    return False


def is_fatal(e: BaseException) -> bool:
    """True for ``e`` or any exception in its cause/context chain (wrappers such as
    LangChain's keep the provider error there)."""
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, FatalAPIError) or _fatal_one(cur):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def raise_if_fatal(e: BaseException) -> None:
    if isinstance(e, FatalAPIError):
        raise e
    if is_fatal(e):
        raise FatalAPIError(f"{type(e).__name__}: {e}") from e
