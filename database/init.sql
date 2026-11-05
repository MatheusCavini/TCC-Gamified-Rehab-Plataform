CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS patients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    protocol TEXT,
    device TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'in_progress',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS sessions_patient_started_idx
    ON sessions (patient_id, started_at DESC);

CREATE TABLE IF NOT EXISTS signal_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    signal_type TEXT NOT NULL CHECK (signal_type IN ('raw', 'filtered')),
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,
    sampling_rate_hz DOUBLE PRECISION NOT NULL CHECK (sampling_rate_hz > 0),
    channels JSONB NOT NULL DEFAULT '[]'::jsonb,
    unit TEXT,
    filter_config JSONB,
    algorithm_version TEXT,
    sha256 CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signal_files_session_idx ON signal_files (session_id);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    value DOUBLE PRECISION NOT NULL,
    unit TEXT,
    algorithm_version TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS performance_metrics_session_idx
    ON performance_metrics (session_id, recorded_at);
