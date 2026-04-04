import redis
import os
from rq import Worker, Queue, Connection
from dotenv import load_dotenv
from app_settings import Config
import logging

# 1. Critical Boot Sequence (Sync with main.py)
load_dotenv()
Config.validate()

# Configure Worker Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("rq_worker")

# Define Queues to listen to
listen = ['high', 'default', 'low']

# 2. Worker Lifecycle Hooks (v16.4.7 Observability)
def report_success(job, connection, result, *args, **kwargs):
    logger.info(f"🏆 JOB SUCCESS: {job.id} | Result: {str(result)[:100]}...")

def report_failure(job, connection, type, value, traceback):
    logger.error(f"💥 JOB FAILED: {job.id} | Error: {value}")

def run_worker():
    """
    Start the RQ worker to process background jobs (Staff+ Hardened).
    This process runs independently of the FastAPI app on Render.
    """
    logger.info("Starting EresumeHub Background Worker...")
    
    try:
        # Initialize Redis Connection SAFELY
        # We use sync redis here for the Worker instance (Library requirement)
        redis_conn = redis.from_url(Config.REDIS_URL, decode_responses=False)
        redis_conn.ping()
        logger.info(f"Worker Redis Connection: ONLINE")
        
        with Connection(redis_conn):
            worker = Worker(
                map(Queue, listen),
                name=f"worker-{os.getpid()}"
            )
            # Register Lifecycle Telemetry
            worker.push_exc_handler(report_failure) 
            logger.info(f"Worker {worker.name} ready for queues: {listen}")
            worker.work(on_success=report_success)
    except Exception as e:
        logger.critical(f"Worker FATAL: {e}")
        raise

if __name__ == '__main__':
    run_worker()
