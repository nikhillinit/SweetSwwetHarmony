"""Production middleware for the Discovery Engine API.

ExceptionHandlerMiddleware — catch-all for unhandled exceptions, returns clean JSON 500.
RequestIdMiddleware — injects X-Request-ID (UUID4) into every request/response.
"""

import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from utils.logging_config import set_request_id

logger = logging.getLogger(__name__)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Inject a unique request ID into every request/response cycle.

    - If the client sends an ``X-Request-ID`` header, it is reused.
    - Otherwise a new UUID4 is generated.
    - The ID is stored on ``request.state.request_id`` for downstream use
      and returned in the ``X-Request-ID`` response header.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        set_request_id(request_id)  # propagate to logging context

        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class ExceptionHandlerMiddleware(BaseHTTPMiddleware):
    """Global catch-all for unhandled exceptions.

    Returns a clean JSON ``{"error": "Internal Server Error", "request_id": ...}``
    response with status 500.  Stack traces are logged server-side but never
    exposed to the client.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            request_id = getattr(request.state, "request_id", None)
            logger.exception(
                "Unhandled exception [request_id=%s] %s %s",
                request_id,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal Server Error",
                    "request_id": request_id,
                },
                headers={_REQUEST_ID_HEADER: request_id} if request_id else {},
            )
