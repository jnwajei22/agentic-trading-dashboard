CREATE TABLE desk.workspace_events (
    id TEXT PRIMARY KEY, user_sub TEXT NOT NULL, event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL, subject_ref TEXT, profile_ref TEXT,
    account_ref TEXT, summary TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX workspace_events_owner_time ON desk.workspace_events(user_sub, occurred_at DESC);
ALTER TABLE desk.workspace_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY desk_runtime_access ON desk.workspace_events TO desk_runtime USING (true) WITH CHECK (true);
REVOKE ALL ON desk.workspace_events FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON desk.workspace_events TO desk_runtime;
