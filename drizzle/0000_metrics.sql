CREATE TABLE records (
 id TEXT PRIMARY KEY NOT NULL,
 kind TEXT NOT NULL,
 turn_id TEXT NOT NULL,
 ts INTEGER NOT NULL,
 model TEXT NOT NULL,
 effort TEXT NOT NULL,
 status TEXT NOT NULL,
 input_tokens INTEGER NOT NULL DEFAULT 0,
 cached_tokens INTEGER NOT NULL DEFAULT 0,
 output_tokens INTEGER NOT NULL DEFAULT 0,
 reasoning_tokens INTEGER NOT NULL DEFAULT 0,
 duration_ms REAL,
 ttft_ms REAL,
 tps REAL,
 payload TEXT NOT NULL,
 updated_at INTEGER NOT NULL
);
--> statement-breakpoint
CREATE INDEX records_kind_ts ON records(kind, ts DESC);
--> statement-breakpoint
CREATE INDEX records_model_ts ON records(model, ts DESC);
--> statement-breakpoint
CREATE INDEX records_turn ON records(turn_id,kind);
--> statement-breakpoint
CREATE TABLE metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
