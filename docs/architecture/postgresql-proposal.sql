-- DESIGN ONLY. Not applied. SQLite remains authoritative.
-- No import framework, migration tables, OAuth authorization state or runtime cutover.
CREATE TABLE users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), email text, display_name text,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','deleted')),
 created_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz
);
CREATE TABLE user_identities (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 issuer text NOT NULL, subject text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(issuer,subject)
);
CREATE INDEX user_identities_owner ON user_identities(user_id);
CREATE TABLE broker_connections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 provider_type text NOT NULL, label text NOT NULL, environment text NOT NULL CHECK(environment IN ('demo','live')),
 endpoint text NOT NULL, username text NOT NULL, credentials_encrypted bytea, encryption_key_version text,
 server text NOT NULL, status text NOT NULL CHECK(status IN ('active','disabled','reauthentication_required')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 legacy_ref text UNIQUE, UNIQUE(user_id,id),
 CHECK((credentials_encrypted IS NULL)=(encryption_key_version IS NULL))
);
CREATE TABLE broker_accounts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 connection_id uuid NOT NULL, broker_account_id text NOT NULL, account_number text NOT NULL,
 alias text NOT NULL, nickname text, currency text, environment text NOT NULL CHECK(environment IN ('demo','live')),
 verified_demo boolean, enabled boolean NOT NULL DEFAULT false, available boolean NOT NULL DEFAULT false,
 is_default boolean NOT NULL DEFAULT false, equity_high_watermark numeric(28,10),
 risk_state jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 legacy_ref text UNIQUE, UNIQUE(user_id,id), UNIQUE(connection_id,broker_account_id,account_number),
 FOREIGN KEY(user_id,connection_id) REFERENCES broker_connections(user_id,id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX broker_account_alias ON broker_accounts(user_id,lower(alias));
CREATE UNIQUE INDEX one_default_account ON broker_accounts(user_id) WHERE is_default;
CREATE TABLE user_preferences (
 user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 appearance text NOT NULL DEFAULT 'system' CHECK(appearance IN ('system','light','dark')),
 timezone text NOT NULL DEFAULT 'America/Chicago', date_format text NOT NULL DEFAULT 'locale',
 time_format text NOT NULL DEFAULT 'locale', currency_display text NOT NULL DEFAULT 'account',
 notifications jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE watchlists (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80), is_default boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), legacy_ref text UNIQUE, UNIQUE(user_id,id)
);
CREATE UNIQUE INDEX watchlist_name ON watchlists(user_id,lower(name));
CREATE UNIQUE INDEX one_default_watchlist ON watchlists(user_id) WHERE is_default;
CREATE TABLE watchlist_items (
 user_id uuid NOT NULL, watchlist_id uuid NOT NULL, canonical_id text NOT NULL,
 position integer NOT NULL CHECK(position>=0), pinned boolean NOT NULL DEFAULT false,
 PRIMARY KEY(watchlist_id,canonical_id), UNIQUE(watchlist_id,position),
 FOREIGN KEY(user_id,watchlist_id) REFERENCES watchlists(user_id,id) ON DELETE CASCADE
);
CREATE TABLE execution_profiles (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 account_id uuid NOT NULL, name text NOT NULL, enabled boolean NOT NULL DEFAULT false,
 schema_version integer NOT NULL DEFAULT 2 CHECK(schema_version=2),
 strategy_ref text NOT NULL, strategy_version text NOT NULL,
 policy jsonb NOT NULL CHECK(jsonb_typeof(policy)='object'),
 decision_provider text NOT NULL CHECK(decision_provider IN ('openai','no_trade')), model_identifier text,
 minimum_confidence numeric(5,4) NOT NULL CHECK(minimum_confidence BETWEEN 0 AND 1),
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0), deleted_at timestamptz,
 legacy_ref text UNIQUE, UNIQUE(user_id,id), UNIQUE(user_id,id,account_id),
 FOREIGN KEY(user_id,account_id) REFERENCES broker_accounts(user_id,id) ON DELETE RESTRICT
);
CREATE INDEX profiles_owner_account ON execution_profiles(user_id,account_id);
CREATE TABLE autonomous_controls (
 user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
 kill_switch boolean NOT NULL DEFAULT true, demo_enabled boolean NOT NULL DEFAULT false,
 live_enabled boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(), reason text
);
CREATE TABLE control_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 control_name text NOT NULL, old_value jsonb NOT NULL, new_value jsonb NOT NULL,
 actor text NOT NULL, source text NOT NULL, reason text, occurred_at timestamptz NOT NULL,
 legacy_ref text UNIQUE
);
CREATE INDEX control_events_owner_time ON control_events(user_id,occurred_at DESC);
CREATE TABLE schedules (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 profile_id uuid NOT NULL, timezone text NOT NULL, recurrence jsonb NOT NULL,
 enabled boolean NOT NULL DEFAULT false, maximum_lateness_seconds integer NOT NULL CHECK(maximum_lateness_seconds BETWEEN 30 AND 3600),
 next_run_at timestamptz, deleted_at timestamptz, legacy_ref text UNIQUE, UNIQUE(user_id,id),
 FOREIGN KEY(user_id,profile_id) REFERENCES execution_profiles(user_id,id) ON DELETE RESTRICT
);
CREATE INDEX schedules_due ON schedules(next_run_at) WHERE enabled AND deleted_at IS NULL;
CREATE TABLE autonomous_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, profile_id uuid NOT NULL, account_id uuid NOT NULL,
 run_key text NOT NULL, trigger_reason text NOT NULL,
 state text NOT NULL CHECK(state IN ('claimed','snapshotting','deciding','validating','previewing','submitting','trade','no_trade','blocked','market_closed','skipped','error','unknown')),
 dry_run boolean NOT NULL DEFAULT false, model_identifier text, context_hash text,
 evidence jsonb NOT NULL DEFAULT '{}', decision jsonb NOT NULL DEFAULT '{}', validation jsonb NOT NULL DEFAULT '{}',
 usage jsonb NOT NULL DEFAULT '{}', reason_codes jsonb NOT NULL DEFAULT '[]',
 started_at timestamptz NOT NULL, completed_at timestamptz, legacy_ref text UNIQUE,
 UNIQUE(user_id,id), UNIQUE(user_id,profile_id,run_key),
 FOREIGN KEY(user_id,profile_id,account_id) REFERENCES execution_profiles(user_id,id,account_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX one_active_profile_run ON autonomous_runs(user_id,profile_id)
 WHERE state IN ('claimed','snapshotting','deciding','validating','previewing','submitting');
CREATE INDEX runs_owner_time ON autonomous_runs(user_id,started_at DESC);
CREATE TABLE schedule_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, schedule_id uuid NOT NULL,
 scheduled_at timestamptz NOT NULL, run_id uuid, state text NOT NULL,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), retry_at timestamptz,
 lease_owner text, lease_until timestamptz, error_category text, legacy_ref text UNIQUE,
 UNIQUE(schedule_id,scheduled_at), UNIQUE(user_id,id),
 FOREIGN KEY(user_id,schedule_id) REFERENCES schedules(user_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(user_id,run_id) REFERENCES autonomous_runs(user_id,id) ON DELETE RESTRICT
);
CREATE INDEX schedule_runs_retry ON schedule_runs(retry_at) WHERE state='retry_scheduled';
CREATE TABLE order_previews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, profile_id uuid NOT NULL, account_id uuid NOT NULL,
 run_id uuid, action_type text NOT NULL CHECK(action_type IN ('place_order','cancel_order','close_position')),
 execution_origin text NOT NULL CHECK(execution_origin IN ('co_op','autonomous','manual','legacy')),
 target_id text, canonical_id text, side text CHECK(side IN ('long','short')), order_type text,
 entry numeric(28,10), stop_loss numeric(28,10), take_profit numeric(28,10), quantity numeric(28,10),
 estimated_risk numeric(28,10), estimated_margin numeric(28,10), request_fingerprint text NOT NULL,
 profile_revision integer NOT NULL, evidence jsonb NOT NULL, validation jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('approved','rejected','expired','consumed')),
 created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 legacy_ref text UNIQUE, UNIQUE(user_id,id), UNIQUE(user_id,id,account_id),
 FOREIGN KEY(user_id,profile_id,account_id) REFERENCES execution_profiles(user_id,id,account_id) ON DELETE RESTRICT,
 FOREIGN KEY(user_id,run_id) REFERENCES autonomous_runs(user_id,id) ON DELETE RESTRICT,
 CHECK(expires_at>created_at), CHECK(quantity IS NULL OR quantity>0),
 CHECK(action_type<>'place_order' OR (canonical_id IS NOT NULL AND side IS NOT NULL AND quantity IS NOT NULL AND stop_loss IS NOT NULL)),
 CHECK(action_type='place_order' OR target_id IS NOT NULL)
);
CREATE INDEX previews_owner_time ON order_previews(user_id,created_at DESC);
CREATE TABLE order_executions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, account_id uuid NOT NULL, preview_id uuid NOT NULL UNIQUE,
 idempotency_key text NOT NULL, request_fingerprint text NOT NULL,
 state text NOT NULL CHECK(state IN ('claimed','submitting','submitted','verified','rejected','unknown','cancelled','closed')),
 broker_order_id text, broker_position_id text, response_sanitized jsonb NOT NULL DEFAULT '{}',
 reconciliation jsonb NOT NULL DEFAULT '{}', reconciliation_events jsonb NOT NULL DEFAULT '[]',
 submitted_at timestamptz, verified_at timestamptz, created_at timestamptz NOT NULL,
 legacy_ref text UNIQUE, UNIQUE(user_id,id), UNIQUE(user_id,idempotency_key),
 FOREIGN KEY(user_id,preview_id,account_id) REFERENCES order_previews(user_id,id,account_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX broker_order_identity ON order_executions(account_id,broker_order_id) WHERE broker_order_id IS NOT NULL;
CREATE INDEX executions_owner_time ON order_executions(user_id,created_at DESC);
CREATE TABLE signal_intents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 profile_id uuid, provider_type text NOT NULL, canonical_id text NOT NULL,
 idempotency_key text NOT NULL, payload jsonb NOT NULL, status text NOT NULL DEFAULT 'untrusted',
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 UNIQUE(user_id,provider_type,idempotency_key),
 FOREIGN KEY(user_id,profile_id) REFERENCES execution_profiles(user_id,id) ON DELETE RESTRICT
);
CREATE INDEX signal_intents_owner_time ON signal_intents(user_id,created_at DESC);
CREATE TABLE platform_runtime_settings (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), configuration jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agent_conversations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 mode text NOT NULL CHECK(mode IN ('research','co_op','autonomous')), profile_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(), busy_until timestamptz,
 UNIQUE(user_id,id), FOREIGN KEY(user_id,profile_id) REFERENCES execution_profiles(user_id,id) ON DELETE RESTRICT
);
CREATE INDEX agent_conversations_owner_time ON agent_conversations(user_id,created_at DESC);
CREATE TABLE agent_messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL, conversation_id uuid NOT NULL,
 sequence bigint GENERATED ALWAYS AS IDENTITY, kind text NOT NULL CHECK(kind IN ('user','assistant','tool','approval','execution')),
 payload jsonb NOT NULL, state text NOT NULL CHECK(state IN ('pending','claimed','complete')),
 preview_id uuid, execution_id uuid, expires_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(conversation_id,sequence),
 FOREIGN KEY(user_id,conversation_id) REFERENCES agent_conversations(user_id,id) ON DELETE CASCADE,
 FOREIGN KEY(user_id,preview_id) REFERENCES order_previews(user_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(user_id,execution_id) REFERENCES order_executions(user_id,id) ON DELETE RESTRICT,
 CHECK(kind<>'approval' OR (preview_id IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE INDEX pending_agent_approvals ON agent_messages(user_id,expires_at) WHERE kind='approval' AND state='pending';

CREATE INDEX connections_owner ON broker_connections(user_id);
CREATE INDEX accounts_owner_connection ON broker_accounts(user_id,connection_id);
CREATE INDEX schedules_owner_profile ON schedules(user_id,profile_id);
CREATE INDEX schedule_runs_owner_schedule ON schedule_runs(user_id,schedule_id,scheduled_at DESC);
CREATE INDEX schedule_runs_run ON schedule_runs(user_id,run_id);
CREATE INDEX previews_owner_profile ON order_previews(user_id,profile_id);
CREATE INDEX previews_run ON order_previews(user_id,run_id);
CREATE INDEX signals_profile ON signal_intents(user_id,profile_id);
CREATE INDEX conversation_profile ON agent_conversations(user_id,profile_id);
CREATE INDEX agent_messages_preview ON agent_messages(user_id,preview_id) WHERE preview_id IS NOT NULL;
CREATE INDEX agent_messages_execution ON agent_messages(user_id,execution_id) WHERE execution_id IS NOT NULL;
