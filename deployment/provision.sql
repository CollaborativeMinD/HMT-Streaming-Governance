-- Operator-only psql migration, after applying service/src/hmt_governance/schema.sql.
-- Supply broker_password through a protected psql variable file, never a shell argument or Git.
CREATE ROLE gov_broker LOGIN PASSWORD :'broker_password';
GRANT USAGE ON SCHEMA governance TO gov_broker;
GRANT SELECT ON ALL TABLES IN SCHEMA governance TO gov_broker;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA governance TO gov_broker;
GRANT INSERT ON governance.sessions,governance.receipts,governance.records,governance.events,governance.effects TO gov_broker;
GRANT UPDATE(state,reason) ON governance.sessions TO gov_broker;
GRANT UPDATE(status,result) ON governance.receipts TO gov_broker;
GRANT UPDATE(state,claim,result,updated_at) ON governance.effects TO gov_broker;
GRANT UPDATE(records,sessions) ON governance.budget TO gov_broker;
GRANT EXECUTE ON FUNCTION governance.lock_agents(text[]),governance.lock_route(text,text) TO gov_broker;
