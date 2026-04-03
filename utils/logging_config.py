import os
import logging
import logging.handlers
from datetime import datetime

def setup_logging():
    """
    Centralized Production Logging Configuration (v16.3.0).
    Configures TimedRotatingFileHandler for daily logs and StreamHandler for console.
    """
    # 1. Ensure Logs Directory
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "api.log")
    
    # 2. Production Formatter (Elite Standard)
    # Format: Timestamp | Level | Module | Message
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s'
    )
    
    # 3. Handlers
    # A. Timed Rotating File Handler (30 Days Retention)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # B. Console Handler for Real-Time Monitoring
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # 4. Root Logger Configuration
    root_logger = logging.getLogger()
    
    # Clear existing handlers to prevent duplicate logs in some environments
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
        
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    # 5. Silence Noisy Third-Party Libs (Staff+ Standard)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("rich").setLevel(logging.WARNING)
    
    logging.info(f"--- 🚀 Production Logging Initialized: {log_file} ---")
