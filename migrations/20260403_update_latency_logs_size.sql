-- Add response_size_kb to track payload discipline (v15.2.0)
ALTER TABLE endpoint_latency_logs 
ADD COLUMN IF NOT EXISTS response_size_kb FLOAT DEFAULT 0;

-- Index for auditing oversized payloads
CREATE INDEX IF NOT EXISTS idx_latency_size ON endpoint_latency_logs (response_size_kb);
