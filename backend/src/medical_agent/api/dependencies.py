"""Request-scoped authentication, authorization, database, and service dependencies."""

import hashlib
import re

from fastapi import Header

_VISITOR_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


async def current_owner_id(x_visitor_id: str | None = Header(default=None)) -> str:
    value = (x_visitor_id or "demo-visitor").strip()
    if not _VISITOR_ID.fullmatch(value):
        value = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
    return value

