import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

class TelemetryService:
    """
    Staff+ Telemetry Service (v16.5.3).
    Implements a worker-pattern queue to process telemetry events 
    without blocking the primary event loop or request cycle.
    """
    def __init__(self):
        self.queue = None  # Will be created in start() (Audit P0)
        self._worker_task = None

    async def start(self):
        """Start the background consumer."""
        if self._worker_task is None:
            # Initialize queue inside the running event loop
            self.queue = asyncio.Queue(maxsize=1000)
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("Telemetry Service: Worker started.")

    async def stop(self):
        """Gracefully stop the worker (Audit Priority 2)."""
        if self._worker_task:
            # 1. Wait for queue to drain (Ensures zero-loss on shutdown)
            if self.queue:
                try:
                    await asyncio.wait_for(self.queue.join(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Telemetry Service: Shutdown timeout, some events may be lost.")
            
            # 2. Cancel worker
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
            logger.info("Telemetry Service: Worker stopped.")

    def enqueue(self, event_type: str, data: Dict[str, Any]):
        """Non-blocking enqueue of telemetry events."""
        if self.queue is None:
            logger.warning(f"Telemetry Service: Not started, dropping event: {event_type}")
            return
            
        try:
            self.queue.put_nowait({"type": event_type, "data": data})
        except asyncio.QueueFull:
            logger.warning("Telemetry Service: Queue full, dropping event.")

    async def _worker(self):
        while True:
            try:
                event = await self.queue.get()
                await self._process_event(event)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telemetry Service Worker Error: {e}")

    async def _process_event(self, event: Dict[str, Any]):
        from app_settings import Config
        try:
            # Current implementation: Async structured logging (Scaffold for future APM)
            etype = event.get("type")
            data = event.get("data", {})
            
            # Elevate SLA breaches to WARNING (Audit Recommendation)
            if etype == "latency" and data.get("ms", 0) > Config.SLA_LATENCY_MS:
                logger.warning(f"Telemetry SLA: {data.get('path')} took {data.get('ms'):.1f}ms (Limit: {Config.SLA_LATENCY_MS}ms)")
            else:
                # Production Debugging (Audit A4: Sampling could be added here later)
                logger.debug(f"Telemetry: Processed {etype} event | {data.get('path', 'unknown')}")
        except Exception as e:
            logger.error(f"Telemetry Worker Event Processing Error: {e}")

telemetry_service = TelemetryService()
