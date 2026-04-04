import time
import logging
import asyncio
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from services.supabase_service import supabase_service

logger = logging.getLogger(__name__)

class LatencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        try:
            # 1. Execute Request
            response = await call_next(request)
            
            # 2. Measure Duration & Size Safely (v16.4.3 Resilience)
            duration_ms = (time.time() - start_time) * 1000
            
            # Use safe header access to prevent TypeError crash
            content_length = response.headers.get("Content-Length")
            try:
                size_kb = round(int(content_length) / 1024, 2) if content_length and content_length.isdigit() else 0
            except (ValueError, TypeError):
                size_kb = 0
            
            # 3. Log Asynchronously (Fire and Forget)
            asyncio.create_task(self._log_performance(request, response.status_code, duration_ms, size_kb))
            
            return response
            
        except Exception as e:
            # 🛡️ Monitoring Isolation (v16.4.3)
            # Monitoring should NEVER crash the primary response cycle.
            # If we hit an unhandled exception here, we let it propagate 
            # to the global_exception_handler which is forced to return JSON in main.py.
            logger.error(f"LatencyMiddleware Failure in request execution: {e}")
            raise

    async def _log_performance(self, request: Request, status_code: int, duration_ms: float, size_kb: float):
        try:
            # Skip noise
            if request.url.path in ['/', '/health', '/favicon.ico']:
                return

            user_id = getattr(request.state, "user_id", None)
            
            log_data = {
                "path": request.url.path,
                "method": request.method,
                "duration_ms": round(duration_ms, 2),
                "response_size_kb": size_kb,
                "status_code": status_code,
                "user_id": user_id
            }
            
            # 1. Persist to Observability Table
            await supabase_service.client.table("endpoint_latency_logs").insert(log_data).execute()
            
            # 2. Production SLA Auditing (v15.2.0 Enforcement)
            is_slow = duration_ms > 300
            is_bloated = size_kb > 50
            
            if is_slow:
                logger.warning(f"🚨 SLA BREACH [LATENCY]: {request.method} {request.url.path} took {round(duration_ms)}ms (Limit: 300ms)")
            
            if is_bloated:
                logger.warning(f"🚨 SLA BREACH [PAYLOAD]: {request.method} {request.url.path} is {size_kb}KB (Limit: 50KB)")
                
        except Exception as e:
            logger.error(f"Performance Auditing Failure: {e}")
