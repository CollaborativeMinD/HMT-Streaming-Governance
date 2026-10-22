CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS governance;
CREATE TABLE governance.meta(version integer PRIMARY KEY CHECK(version=1));
INSERT INTO governance.meta VALUES(1);
CREATE TABLE governance.agents(
 agent_id text PRIMARY KEY, peer_key text UNIQUE NOT NULL CHECK(peer_key ~ '^nodekey:[0-9a-f]{64}$'),
 epoch bigint NOT NULL CHECK(epoch>0), active boolean NOT NULL DEFAULT true,
 expires_at timestamptz NOT NULL, roles text[] NOT NULL);
CREATE TABLE governance.routes(source_agent text REFERENCES governance.agents,target_agent text REFERENCES governance.agents,
 active boolean NOT NULL DEFAULT true,PRIMARY KEY(source_agent,target_agent));
CREATE TABLE governance.budget(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 records bigint NOT NULL DEFAULT 0, sessions bigint NOT NULL DEFAULT 0);
INSERT INTO governance.budget(singleton) VALUES(true);
CREATE TABLE governance.sessions(
 session_id text PRIMARY KEY,source_agent text NOT NULL REFERENCES governance.agents,
 target_agent text NOT NULL REFERENCES governance.agents,source_epoch bigint NOT NULL,target_epoch bigint NOT NULL,
 state text NOT NULL CHECK(state IN ('ACTIVE','HELD','QUARANTINED')),reason text,
 sequence_number bigint NOT NULL DEFAULT -1,head_hash text NOT NULL DEFAULT repeat('0',64));
CREATE TABLE governance.receipts(
 receipt_id uuid PRIMARY KEY,admission_index bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 session_id text NOT NULL REFERENCES governance.sessions,request_key text NOT NULL,
 peer_key text NOT NULL,frame jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,status text NOT NULL CHECK(status IN ('PENDING','VERIFIED','HELD','QUARANTINED')),
 result jsonb,UNIQUE(session_id,request_key));
CREATE INDEX receipt_pending ON governance.receipts(admission_index) WHERE status='PENDING';
CREATE TABLE governance.records(
 session_id text NOT NULL REFERENCES governance.sessions,sequence_number bigint NOT NULL,
 receipt_id uuid NOT NULL UNIQUE REFERENCES governance.receipts,payload text NOT NULL CHECK(octet_length(payload)<=1000000),
 source_agent text NOT NULL,target_agent text NOT NULL,previous_hash text NOT NULL,
 payload_hash text NOT NULL,block_hash text NOT NULL UNIQUE,committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(session_id,sequence_number));
CREATE TABLE governance.events(
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,session_id text NOT NULL REFERENCES governance.sessions,
 kind text NOT NULL,detail text NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE governance.effects(
 effect_id uuid PRIMARY KEY,session_id text NOT NULL,sequence_number bigint NOT NULL,effect_key text NOT NULL,
 state text NOT NULL CHECK(state IN ('PREPARED','DISPATCHING','COMPLETED','UNKNOWN','FAILED_NO_EFFECT')),
 claim uuid,block_hash text NOT NULL,result jsonb,updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(session_id,sequence_number),FOREIGN KEY(session_id,sequence_number) REFERENCES governance.records);
CREATE FUNCTION governance.immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'APPEND_ONLY' USING ERRCODE='P0001'; END $$;
CREATE TRIGGER immutable_records BEFORE UPDATE OR DELETE ON governance.records FOR EACH ROW EXECUTE FUNCTION governance.immutable();
CREATE TRIGGER immutable_events BEFORE UPDATE OR DELETE ON governance.events FOR EACH ROW EXECUTE FUNCTION governance.immutable();
CREATE FUNCTION governance.chain_guard() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,governance,public AS $$
DECLARE s governance.sessions; r governance.receipts; ph text; bh text; material text;
BEGIN
 SELECT * INTO s FROM governance.sessions WHERE session_id=NEW.session_id FOR UPDATE;
 SELECT * INTO r FROM governance.receipts WHERE receipt_id=NEW.receipt_id;
 IF s.state<>'ACTIVE' OR r.status<>'PENDING' OR r.expires_at<=clock_timestamp() THEN
  RAISE EXCEPTION 'SESSION_OR_RECEIPT_HELD' USING ERRCODE='P0002'; END IF;
 IF NEW.sequence_number<>s.sequence_number+1 OR NEW.previous_hash<>s.head_hash THEN
  RAISE EXCEPTION 'CHAIN_MISMATCH' USING ERRCODE='P0003'; END IF;
 IF NEW.source_agent<>s.source_agent OR NEW.target_agent<>s.target_agent OR
    NEW.payload<>r.frame->>'payload' OR NEW.session_id<>r.session_id OR
    NEW.sequence_number<>(r.frame->>'sequence_number')::bigint OR
    NEW.previous_hash<>r.frame->>'previous_hash' OR
    NEW.source_agent<>r.frame->>'source_agent' OR NEW.target_agent<>r.frame->>'target_entity' THEN
  RAISE EXCEPTION 'FRAME_MISMATCH' USING ERRCODE='P0003'; END IF;
 IF NOT EXISTS(SELECT 1 FROM governance.agents WHERE agent_id=s.source_agent AND peer_key=r.peer_key
   AND epoch=s.source_epoch AND active AND expires_at>clock_timestamp()) OR
    NOT EXISTS(SELECT 1 FROM governance.agents WHERE agent_id=s.target_agent AND epoch=s.target_epoch AND active
   AND expires_at>clock_timestamp()) OR NOT EXISTS(SELECT 1 FROM governance.routes WHERE source_agent=s.source_agent
   AND target_agent=s.target_agent AND active) THEN
  RAISE EXCEPTION 'AUTHORITY_CHANGED' USING ERRCODE='P0002'; END IF;
 ph=encode(digest(convert_to(NEW.payload,'UTF8'),'sha256'),'hex');
 material=format('{"payload_hash":%s,"previous_hash":%s,"sequence_number":%s,"session_id":%s,"source_agent":%s,"target_entity":%s,"version":1}',
  to_json(ph),to_json(NEW.previous_hash),NEW.sequence_number,to_json(NEW.session_id),to_json(NEW.source_agent),to_json(NEW.target_agent));
 bh=encode(digest(convert_to(material,'UTF8'),'sha256'),'hex');
 IF NEW.payload_hash<>ph OR NEW.block_hash<>bh OR
    (r.frame->>'expected_hash' IS NOT NULL AND r.frame->>'expected_hash'<>ph) THEN
  RAISE EXCEPTION 'HASH_MISMATCH' USING ERRCODE='P0003'; END IF;
 UPDATE governance.budget SET records=records+1 WHERE singleton;
 UPDATE governance.sessions SET sequence_number=NEW.sequence_number,head_hash=bh WHERE session_id=NEW.session_id;
 RETURN NEW;
END $$;
CREATE TRIGGER chain_guard BEFORE INSERT ON governance.records FOR EACH ROW EXECUTE FUNCTION governance.chain_guard();
REVOKE ALL ON SCHEMA governance FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA governance FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA governance FROM PUBLIC;
CREATE FUNCTION governance.lock_agents(names text[]) RETURNS SETOF governance.agents
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,governance AS
 $$SELECT * FROM governance.agents WHERE agent_id=ANY(names) ORDER BY agent_id FOR SHARE$$;
CREATE FUNCTION governance.lock_route(source_id text,target_id text) RETURNS SETOF governance.routes
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,governance AS
 $$SELECT * FROM governance.routes WHERE source_agent=source_id AND target_agent=target_id FOR SHARE$$;
REVOKE ALL ON FUNCTION governance.lock_agents(text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION governance.lock_route(text,text) FROM PUBLIC;
CREATE FUNCTION governance.session_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.state='QUARANTINED' AND NEW.state<>'QUARANTINED') OR
    (OLD.state='HELD' AND NEW.state NOT IN ('HELD','QUARANTINED')) THEN
  RAISE EXCEPTION 'TERMINAL_SESSION' USING ERRCODE='P0001'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER session_guard BEFORE UPDATE ON governance.sessions FOR EACH ROW EXECUTE FUNCTION governance.session_guard();
CREATE FUNCTION governance.receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r governance.records;
BEGIN
 IF OLD.status<>'PENDING' AND (NEW.status<>OLD.status OR NEW.result IS DISTINCT FROM OLD.result) THEN
  RAISE EXCEPTION 'IMMUTABLE_RECEIPT' USING ERRCODE='P0001'; END IF;
 IF NEW.status='VERIFIED' THEN
  SELECT * INTO r FROM governance.records WHERE receipt_id=NEW.receipt_id;
  IF NOT FOUND OR NEW.result->>'ledger_hash' IS DISTINCT FROM r.block_hash OR NEW.result->>'payload_hash' IS DISTINCT FROM r.payload_hash OR
     NEW.result->'transaction' IS DISTINCT FROM NEW.frame THEN
   RAISE EXCEPTION 'UNREGISTERED_RECEIPT' USING ERRCODE='P0001'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER receipt_guard BEFORE UPDATE ON governance.receipts FOR EACH ROW EXECUTE FUNCTION governance.receipt_guard();
CREATE FUNCTION governance.effect_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('COMPLETED','FAILED_NO_EFFECT') AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'TERMINAL_EFFECT' USING ERRCODE='P0001'; END IF;
 IF NOT ((OLD.state='PREPARED' AND NEW.state IN ('PREPARED','DISPATCHING')) OR
         (OLD.state='DISPATCHING' AND NEW.state IN ('DISPATCHING','COMPLETED','UNKNOWN','FAILED_NO_EFFECT')) OR
         (OLD.state='UNKNOWN' AND NEW.state IN ('UNKNOWN','COMPLETED','FAILED_NO_EFFECT')) OR
         (OLD.state IN ('COMPLETED','FAILED_NO_EFFECT') AND NEW.state=OLD.state)) THEN
  RAISE EXCEPTION 'INVALID_EFFECT_TRANSITION' USING ERRCODE='P0001'; END IF;
 IF (OLD.claim IS NOT NULL AND NEW.claim IS DISTINCT FROM OLD.claim) OR
    (NEW.state='DISPATCHING' AND NEW.claim IS NULL) THEN
  RAISE EXCEPTION 'INVALID_DISPATCH_CLAIM' USING ERRCODE='P0001'; END IF;
 IF NEW.state IN ('COMPLETED','FAILED_NO_EFFECT') AND
    (NEW.result->>'effect_id' IS DISTINCT FROM NEW.effect_id::text OR
     NEW.result->>'block_hash' IS DISTINCT FROM NEW.block_hash OR NEW.result->>'state' IS DISTINCT FROM NEW.state) THEN
  RAISE EXCEPTION 'UNBOUND_EFFECT_WITNESS' USING ERRCODE='P0001'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER effect_guard BEFORE UPDATE ON governance.effects FOR EACH ROW EXECUTE FUNCTION governance.effect_guard();
REVOKE ALL ON FUNCTION governance.session_guard(),governance.receipt_guard(),governance.effect_guard() FROM PUBLIC;

CREATE FUNCTION governance.effect_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state<>'PREPARED' OR NEW.claim IS NOT NULL OR NEW.result IS NOT NULL OR NOT EXISTS(
   SELECT 1 FROM governance.records WHERE session_id=NEW.session_id AND sequence_number=NEW.sequence_number AND block_hash=NEW.block_hash) THEN
  RAISE EXCEPTION 'INVALID_EFFECT_INTENT' USING ERRCODE='P0001'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER effect_insert_guard BEFORE INSERT ON governance.effects FOR EACH ROW EXECUTE FUNCTION governance.effect_insert_guard();
REVOKE ALL ON FUNCTION governance.effect_insert_guard() FROM PUBLIC;
