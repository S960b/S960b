"""Phase 4 forward-only ledger schema (DDL only; caller owns DB lifecycle).

No filesystem, network, connection initialization or MultiTool calls here.
All research evidence tables are append-only. A separate recovery workflow,
never a model-prompt replay, must resolve unknown attempts.
"""

SCHEMA_VERSION = 4
ARMS = ('news', 'news_market', 'news_market_social')
SCHEMA_SQL = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_observations(
 fingerprint TEXT PRIMARY KEY CHECK(length(fingerprint)=64),
 first_seen_ms INTEGER NOT NULL CHECK(first_seen_ms>0),
 news_json TEXT NOT NULL CHECK(length(news_json) BETWEEN 1 AND 4000),
 news_digest TEXT NOT NULL CHECK(length(news_digest)=64));
CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY, cutoff_ms INTEGER NOT NULL CHECK(cutoff_ms>0),
 created_ms INTEGER NOT NULL CHECK(created_ms>=cutoff_ms),
 news_hash TEXT NOT NULL CHECK(length(news_hash)=64),
 feature_version TEXT NOT NULL, provenance_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claimed_events(
 fingerprint TEXT PRIMARY KEY REFERENCES news_observations(fingerprint),
 event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id),
 claimed_ms INTEGER NOT NULL CHECK(claimed_ms>0));
CREATE TABLE IF NOT EXISTS features(
 event_id TEXT NOT NULL REFERENCES events(event_id),
 source TEXT NOT NULL CHECK(source IN ('news','market','social')),
 available_ms INTEGER NOT NULL CHECK(available_ms>0),
 observed_ms INTEGER NOT NULL CHECK(observed_ms>=available_ms),
 digest TEXT NOT NULL CHECK(length(digest)=64),
 PRIMARY KEY(event_id,source));
CREATE TABLE IF NOT EXISTS source_missing(
 event_id TEXT NOT NULL REFERENCES events(event_id),
 source TEXT NOT NULL CHECK(source IN ('market','social')),
 checked_ms INTEGER NOT NULL CHECK(checked_ms>0),
 reason TEXT NOT NULL CHECK(reason IN ('unavailable','denied','rate_limited','no_data','clock_mismatch','incomplete')),
 PRIMARY KEY(event_id,source));
CREATE TABLE IF NOT EXISTS attempt_reservations(
 attempt_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id),
 arm TEXT NOT NULL CHECK(arm IN ('news','news_market','news_market_social')),
 reserved_ms INTEGER NOT NULL, input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
 UNIQUE(event_id,arm));
CREATE TABLE IF NOT EXISTS attempt_sessions(
 attempt_id TEXT PRIMARY KEY REFERENCES attempt_reservations(attempt_id),
 session_id TEXT NOT NULL CHECK(length(session_id) BETWEEN 5 AND 160),
 bound_ms INTEGER NOT NULL CHECK(bound_ms>0));
CREATE TABLE IF NOT EXISTS attempt_status_events(
 ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
 attempt_id TEXT NOT NULL REFERENCES attempt_reservations(attempt_id),
 status TEXT NOT NULL CHECK(status IN ('reserved','session_created','prompt_attempted','completed','failed','unknown')),
 ts_ms INTEGER NOT NULL CHECK(ts_ms>0));
CREATE TABLE IF NOT EXISTS decisions(
 event_id TEXT NOT NULL REFERENCES events(event_id),
 arm TEXT NOT NULL CHECK(arm IN ('news','news_market','news_market_social')),
 direction TEXT NOT NULL CHECK(direction IN ('UP','DOWN','NEUTRAL')),
 confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
 provider_id TEXT NOT NULL, model_id TEXT NOT NULL,
 completed_ms INTEGER NOT NULL, response_hash TEXT NOT NULL CHECK(length(response_hash)=64),
 PRIMARY KEY(event_id,arm), FOREIGN KEY(event_id,arm) REFERENCES attempt_reservations(event_id,arm));
CREATE TABLE IF NOT EXISTS entry_quotes(
 event_id TEXT PRIMARY KEY REFERENCES events(event_id),
 entry_ms INTEGER NOT NULL, quote_observed_ms INTEGER NOT NULL,
 entry_price REAL NOT NULL CHECK(entry_price>0), source TEXT NOT NULL CHECK(source='binance_public'));
CREATE TABLE IF NOT EXISTS outcomes(
 event_id TEXT NOT NULL REFERENCES entry_quotes(event_id),
 horizon_h INTEGER NOT NULL CHECK(horizon_h IN (1,4,24)),
 exit_ms INTEGER NOT NULL, settled_ms INTEGER NOT NULL,
 exit_price REAL NOT NULL CHECK(exit_price>0),
 PRIMARY KEY(event_id,horizon_h));
CREATE TRIGGER IF NOT EXISTS p4_claim_before_event BEFORE INSERT ON claimed_events BEGIN
 SELECT CASE WHEN NEW.claimed_ms < (SELECT first_seen_ms FROM news_observations
  WHERE fingerprint=NEW.fingerprint)
 THEN RAISE(ABORT,'claim_before_first_seen') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_features_pre_cutoff BEFORE INSERT ON features BEGIN
 SELECT CASE WHEN NEW.available_ms > (SELECT cutoff_ms FROM events WHERE event_id=NEW.event_id)
 OR NEW.observed_ms > (SELECT cutoff_ms FROM events WHERE event_id=NEW.event_id)
 THEN RAISE(ABORT,'future_feature') END;
 SELECT CASE WHEN EXISTS (SELECT 1 FROM source_missing WHERE event_id=NEW.event_id AND source=NEW.source)
 THEN RAISE(ABORT,'source_missing_conflict') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_missing_no_feature BEFORE INSERT ON source_missing BEGIN
 SELECT CASE WHEN NEW.checked_ms > (SELECT cutoff_ms FROM events WHERE event_id=NEW.event_id)
 THEN RAISE(ABORT,'missing_checked_after_cutoff') END;
 SELECT CASE WHEN EXISTS (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source=NEW.source)
 THEN RAISE(ABORT,'source_feature_conflict') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_reservation_gate BEFORE INSERT ON attempt_reservations BEGIN
 SELECT CASE WHEN NEW.reserved_ms < (SELECT cutoff_ms FROM events WHERE event_id=NEW.event_id)
 THEN RAISE(ABORT,'reservation_before_cutoff') END;
 SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source='news')
 THEN RAISE(ABORT,'news_feature_missing') END;
 SELECT CASE WHEN NEW.arm IN ('news_market','news_market_social') AND NOT EXISTS
 (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source='market')
 THEN RAISE(ABORT,'market_feature_missing_at_reservation') END;
 SELECT CASE WHEN NEW.arm='news_market_social' AND NOT EXISTS
 (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source='social')
 THEN RAISE(ABORT,'social_feature_missing_at_reservation') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_session_after_reservation BEFORE INSERT ON attempt_sessions BEGIN
 SELECT CASE WHEN NEW.bound_ms < (SELECT reserved_ms FROM attempt_reservations WHERE attempt_id=NEW.attempt_id)
 THEN RAISE(ABORT,'session_before_reservation') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_decision_time BEFORE INSERT ON decisions BEGIN
 SELECT CASE WHEN NEW.completed_ms < (SELECT reserved_ms FROM attempt_reservations WHERE event_id=NEW.event_id AND arm=NEW.arm)
 THEN RAISE(ABORT,'decision_before_reservation') END;
 SELECT CASE WHEN NEW.completed_ms < (SELECT cutoff_ms FROM events WHERE event_id=NEW.event_id)
 THEN RAISE(ABORT,'decision_before_cutoff') END;
 SELECT CASE WHEN NEW.arm='news_market' AND NOT EXISTS
 (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source='market')
 THEN RAISE(ABORT,'market_feature_missing') END;
 SELECT CASE WHEN NEW.arm='news_market_social' AND NOT EXISTS
 (SELECT 1 FROM features WHERE event_id=NEW.event_id AND source='social')
 THEN RAISE(ABORT,'social_feature_missing') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM decisions WHERE event_id=NEW.event_id
 AND (provider_id != NEW.provider_id OR model_id != NEW.model_id))
 THEN RAISE(ABORT,'model_confounded') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_quote_after_decisions BEFORE INSERT ON entry_quotes BEGIN
 SELECT CASE WHEN (SELECT COUNT(*) FROM decisions WHERE event_id=NEW.event_id
 AND arm IN ('news','news_market')) != 2 THEN RAISE(ABORT,'unpaired_event') END;
 SELECT CASE WHEN NEW.entry_ms < (SELECT MAX(completed_ms) FROM decisions WHERE event_id=NEW.event_id)
 OR NEW.quote_observed_ms < NEW.entry_ms THEN RAISE(ABORT,'quote_before_decisions') END;
END;
CREATE TRIGGER IF NOT EXISTS p4_outcome_due BEFORE INSERT ON outcomes BEGIN
 SELECT CASE WHEN NEW.exit_ms < (SELECT entry_ms FROM entry_quotes WHERE event_id=NEW.event_id) + NEW.horizon_h * 3600000
 OR NEW.settled_ms < (SELECT entry_ms FROM entry_quotes WHERE event_id=NEW.event_id) + NEW.horizon_h * 3600000 + 120000
 OR NEW.settled_ms < NEW.exit_ms THEN RAISE(ABORT,'early_outcome') END;
END;
'''
for _table in ('news_observations','claimed_events','events','features','source_missing','attempt_reservations',
               'attempt_sessions','attempt_status_events','decisions','entry_quotes','outcomes'):
    SCHEMA_SQL += f"""
CREATE TRIGGER IF NOT EXISTS p4_no_update_{_table} BEFORE UPDATE ON {_table}
BEGIN SELECT RAISE(ABORT,'append_only'); END;
CREATE TRIGGER IF NOT EXISTS p4_no_delete_{_table} BEFORE DELETE ON {_table}
BEGIN SELECT RAISE(ABORT,'append_only'); END;
"""

