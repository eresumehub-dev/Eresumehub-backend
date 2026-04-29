import time
import logging
from starlette.types import ASGIApp, Scope, Receive, Send
from services.telemetry_service import telemetry_service

logger = logging.getLogger(__name__)

class LatencyMiddleware:
    """
    Staff+ Latency & Performance Middleware (v16.5.6).
    Final non-blocking ASGI implementation with direct queue dispatch.
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        start_time = time.perf_counter()
        status_code = 500
        content_length = 0

        async def send_wrapper(message):
            nonlocal status_code, content_length
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
                for key, value in message.get("headers", []):
                    if key == b"content-length":
                        try:
                            content_length = int(value.decode("latin-1"))
                        except ValueError: 
                            pass
                        break
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            payload = {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": status_code,
                "ms": duration_ms,
                "kb": round(content_length / 1024, 2)
            }
            
            # RESOLVED: Direct call to non-blocking telemetry queue (Audit Fix)
            # asyncio.Queue.put_nowait is fast and safe here as we are on the main event loop thread.
            # No create_task needed, avoiding GC and thread-safety risks.
            telemetry_service.enqueue("latency", payload)

            self._audit_sla(scope, status_code, duration_ms, content_length / 1024)

    def _audit_sla(self, scope, status, ms, kb):
        from app_settings import Config
        try:
            path = scope.get("path", "")
            if path in ('/health', '/api/health', '/'):
                return

            method = scope.get("method", "unknown")
            
            # Use configurable thresholds (Audit Recommendation)
            if ms > Config.SLA_LATENCY_MS:
                logger.warning(f"🚨 SLA BREACH [LATENCY]: {method} {path} took {round(ms)}ms (Limit: {Config.SLA_LATENCY_MS}ms)")
                
            if kb > Config.SLA_PAYLOAD_KB:
                logger.warning(f"🚨 SLA BREACH [PAYLOAD]: {method} {path} is {round(kb, 2)}KB (Limit: {Config.SLA_PAYLOAD_KB}KB)")
        except Exception as e:
            logger.error(f"SLA Audit Failed: {e}")
