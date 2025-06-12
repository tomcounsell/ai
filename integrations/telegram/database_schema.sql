-- Database schema for unified message handling system
-- Add these tables to system.db

-- Chat messages table for conversation history
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    username TEXT,
    text TEXT,
    is_bot_message BOOLEAN DEFAULT 0,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chat_id, message_id)
);

-- Index for efficient history queries
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_timestamp
ON chat_messages(chat_id, timestamp DESC);

-- Message processing metrics
CREATE TABLE IF NOT EXISTS message_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    hour INTEGER NOT NULL,
    message_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    success_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    total_processing_time REAL DEFAULT 0,
    avg_processing_time REAL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date, hour, message_type, priority)
);

-- Error tracking table
CREATE TABLE IF NOT EXISTS processing_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    chat_id INTEGER,
    username TEXT,
    error_category TEXT NOT NULL,
    error_type TEXT NOT NULL,
    error_message TEXT,
    severity TEXT NOT NULL,
    retry_count INTEGER DEFAULT 0,
    resolved BOOLEAN DEFAULT 0,
    resolution_time DATETIME,
    metadata TEXT, -- JSON
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Index for error analysis
CREATE INDEX IF NOT EXISTS idx_processing_errors_timestamp
ON processing_errors(timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_processing_errors_category
ON processing_errors(error_category, timestamp DESC);

-- Rate limit tracking
CREATE TABLE IF NOT EXISTS rate_limits (
    chat_id INTEGER PRIMARY KEY,
    message_count INTEGER DEFAULT 0,
    window_start DATETIME NOT NULL,
    last_reset DATETIME DEFAULT CURRENT_TIMESTAMP,
    violations INTEGER DEFAULT 0
);

-- Feature flags for gradual migration
CREATE TABLE IF NOT EXISTS feature_flags (
    flag_name TEXT PRIMARY KEY,
    enabled BOOLEAN DEFAULT 0,
    rollout_percentage INTEGER DEFAULT 0,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Insert default feature flags
INSERT OR IGNORE INTO feature_flags (flag_name, enabled, rollout_percentage, description) VALUES
('unified_message_processor', 1, 100, 'Use new unified message processing pipeline'),
('legacy_fallback', 0, 0, 'Enable fallback to legacy handler on errors'),
('intent_classification', 1, 100, 'Enable intent classification for messages'),
('advanced_error_handling', 1, 100, 'Use advanced error categorization and recovery');
