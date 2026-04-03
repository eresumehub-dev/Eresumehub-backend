-- Migration: Add Endpoint Latency Logs (v15.1.0)
-- Objective: Track real-world performance for production-grade observability

CREATE TABLE IF NOT EXISTS endpoint_latency_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    path TEXT NOT NULL,
    method TEXT NOT NULL,
    duration_ms FLOAT NOT NULL,
    status_code INTEGER NOT NULL,
    user_id UUID,
    query_count INTEGER DEFAULT 0, -- Track number of DB calls if possible
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for performance analysis
CREATE INDEX idx_latency_path ON endpoint_latency_logs(path);
CREATE INDEX idx_latency_created_at ON endpoint_latency_logs(created_at);

-- Add comment for documentation
COMMENT ON TABLE endpoint_latency_logs IS 'Tracks performance metrics for all API endpoints to ensure sub-300ms SLA.';
