# middleware/request_id_middleware.py

import uuid
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Staff+ Request-ID & Fault-Tolerance Middleware (v16.4.4).
    Standardized Class-based implementation to resolve Starlette Functional-vs-Class conflicts.
    """
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        try:
            # 1. Pipeline Execution
            response = await call_next(request)
            
            # 2. Response Tagging
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-App-Version"] = "16.4.15"
                return response
            else:
                # 🛡️ NoneType Guard (v16.4.4 Resilience)
                logger.error(f"[{request_id}] Route returned None response.")
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": "Empty response from handler",
                        "request_id": request_id
                    }
                )
                
        except Exception as e:
            # 🛡️ Global Convergence Catch (v16.4.4)
            # Monitoring should NEVER crash the primary response cycle.
            logger.error(f"[{request_id}] Middleware ID-Trap caught fatal crash: {str(e)}")
            
            # We return a structured JSON to prevent socket drops
            return JSONResponse(
                status_code=500,
                content={
                    "success": False, 
                    "error": f"Fatal Middleware Crash: {str(e)}", 
                    "request_id": request_id
                }
            )
