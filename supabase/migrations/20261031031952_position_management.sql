CREATE TABLE desk.position_management_state (
    user_sub TEXT NOT NULL, profile_ref TEXT NOT NULL, position_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'idle' CHECK(state IN ('idle','dispatching','unknown','closed')),
    token TEXT, desired_stop NUMERIC(30,12), action_id TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY(user_sub,profile_ref,position_id)
);
ALTER TABLE desk.position_management_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY desk_runtime_access ON desk.position_management_state TO desk_runtime USING (true) WITH CHECK (true);
REVOKE ALL ON desk.position_management_state FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE,DELETE ON desk.position_management_state TO desk_runtime;
