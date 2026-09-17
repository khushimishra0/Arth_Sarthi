"""Content hashing — the only trace an uploaded file is allowed to leave.

Two jobs, one function. It is the cache key that stops the same scam screenshot,
forwarded round a family group and sent to us by six people, costing six vision
calls. And it is what lets an analysis be stored at all: the row says *which*
image was analysed without the image being recoverable from the row.
"""

from __future__ import annotations

import hashlib

__all__ = ["content_hash"]


def content_hash(data: bytes | str) -> str:
    """SHA-256 hex digest of the input.

    Text is normalised — stripped and whitespace-collapsed — so that the same
    forwarded message pasted with a stray trailing newline still hits cache.

    >>> content_hash("hello")[:12]
    '2cf24dba5fb0'
    """
    if isinstance(data, str):
        data = " ".join(data.split()).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
