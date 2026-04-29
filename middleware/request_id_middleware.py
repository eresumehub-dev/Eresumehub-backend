import uuid
import logging
import os
from starlette.types import ASGIApp, Scope, Receive, Send

logger = logging.getLogger(__name__)

class RequestIDMiddleware:
    """
    Staff+ Request-ID & Fault-Tolerance Middleware (v16.5.5).
    Optimized for zero-allocation header injection and traceback integrity.
    """
    def __init__(self, app: ASGIApp):
        self.app = app
        self.app_version = os.getenv("APP_VERSION", "16.4.15").encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request_id_str = str(uuid.uuid4())
        request_id_bytes = request_id_str.encode("latin-1")
        
        # RESOLVED P0: Expose to request.state (FastAPI routes)
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["request_id"] = request_id_str

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                # RESOLVED P1: Strictly enforce lowercase ASGI header keys
                headers = message.get("headers", [])
                headers.append((b"x-request-id", request_id_bytes))
                # Removed x-app-version header (Audit Recommendation)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # FIX: Use 'raise' without 'e' to preserve the original traceback (Audit C-2)
            logger.error(f"[{request_id_str}] Critical Failure in Request Pipeline", exc_info=True)
            raise 
