import os
import sys

def check_logs():
    print(f"Current working directory: {os.getcwd()}")
    log_dir = "logs"
    if not os.path.exists(log_dir):
        print(f"Logs directory '{log_dir}' does not exist.")
        # Try to find it
        for root, dirs, files in os.walk("."):
            if "logs" in dirs:
                print(f"Found logs directory at: {os.path.join(root, 'logs')}")
                log_dir = os.path.join(root, "logs")
                break
    
    if os.path.exists(log_dir):
        files = os.listdir(log_dir)
        print(f"Files in {log_dir}: {files}")
        for file in files:
            if file.endswith(".log"):
                path = os.path.join(log_dir, file)
                print(f"--- CONTENT OF {path} (Last 50 lines) ---")
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in lines[-50:]:
                        print(line.strip())
    else:
        print("Could not find any logs directory.")

if __name__ == "__main__":
    check_logs()
