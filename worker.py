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

def run_worker():
    """
    Start the RQ worker to process background jobs (Staff+ Hardened).
    This process runs independently of the FastAPI app on Render.
    """
    logger.info("Starting EresumeHub Background Worker...")
    
    try:
        # Initialize Redis Connection SAFELY
        redis_conn = redis.from_url(Config.REDIS_URL, decode_responses=False)
        
        with Connection(redis_conn):
            worker = Worker(map(Queue, listen))
            worker.work()
    except Exception as e:
        logger.critical(f"Worker FATAL: {e}")
        raise

if __name__ == '__main__':
    run_worker()
