PRAGMA foreign_keys = ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE requirements(id TEXT PRIMARY KEY, description TEXT NOT NULL);
CREATE TABLE invariants(id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL REFERENCES requirements,
 description TEXT NOT NULL);
CREATE TABLE failure_modes(id TEXT PRIMARY KEY, description TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('positive_control','failure_challenge')));
CREATE TABLE gates(id TEXT PRIMARY KEY, phase TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE obligations(id TEXT PRIMARY KEY, case_name TEXT NOT NULL,
 invariant_id TEXT NOT NULL REFERENCES invariants, failure_id TEXT NOT NULL REFERENCES failure_modes,
 gate_id TEXT NOT NULL REFERENCES gates, rationale TEXT NOT NULL, expected_json TEXT NOT NULL,
 UNIQUE(invariant_id,failure_id,gate_id));
CREATE TABLE assertions(id TEXT PRIMARY KEY, framework TEXT NOT NULL, test_selector TEXT NOT NULL,
 requirement_id TEXT NOT NULL REFERENCES requirements, obligation_id TEXT REFERENCES obligations,
 categories TEXT NOT NULL);
CREATE TABLE assertion_fields(assertion_id TEXT NOT NULL REFERENCES assertions, field TEXT NOT NULL,
 expected_json TEXT NOT NULL, PRIMARY KEY(assertion_id,field));
CREATE TABLE test_runs(id TEXT PRIMARY KEY, framework TEXT NOT NULL, source_seal TEXT NOT NULL,
 evidence_path TEXT NOT NULL, historical INTEGER NOT NULL CHECK(historical IN (0,1)));
CREATE TABLE test_results(run_id TEXT NOT NULL REFERENCES test_runs,
 assertion_id TEXT NOT NULL REFERENCES assertions, status TEXT NOT NULL CHECK(status IN ('PASS','FAIL','ERROR','SKIP')),
 duration_seconds REAL NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(run_id,assertion_id));
CREATE TABLE supersessions(failed_run_id TEXT NOT NULL, failed_assertion_id TEXT NOT NULL,
 satisfying_run_id TEXT NOT NULL, satisfying_assertion_id TEXT NOT NULL,
 requirement_id TEXT NOT NULL REFERENCES requirements, explanation TEXT NOT NULL,
 FOREIGN KEY(failed_run_id,failed_assertion_id) REFERENCES test_results(run_id,assertion_id),
 FOREIGN KEY(satisfying_run_id,satisfying_assertion_id) REFERENCES test_results(run_id,assertion_id),
 PRIMARY KEY(failed_run_id,failed_assertion_id));
CREATE TABLE mutations(id TEXT PRIMARY KEY, requirement_id TEXT NOT NULL REFERENCES requirements,
 assertion_id TEXT NOT NULL REFERENCES assertions, mutation TEXT NOT NULL, outcome TEXT NOT NULL,
 evidence_path TEXT NOT NULL, source_sha256 TEXT NOT NULL);
CREATE TABLE mutation_supersessions(mutation_id TEXT PRIMARY KEY REFERENCES mutations,
 prior_assertion_id TEXT NOT NULL REFERENCES assertions, superseding_assertion_id TEXT NOT NULL REFERENCES assertions,
 requirement_id TEXT NOT NULL REFERENCES requirements, prior_outcome TEXT NOT NULL, new_outcome TEXT NOT NULL,
 prior_evidence TEXT NOT NULL, explanation TEXT NOT NULL);
CREATE TABLE cross_findings(id INTEGER PRIMARY KEY, invariant_id TEXT NOT NULL REFERENCES invariants,
 failure_id TEXT NOT NULL REFERENCES failure_modes, gate_id TEXT NOT NULL REFERENCES gates,
 obligation_id TEXT REFERENCES obligations, scope_reason TEXT NOT NULL,
 UNIQUE(invariant_id,failure_id,gate_id));
CREATE VIEW cross_report AS
SELECT c.*,i.requirement_id,i.description AS invariant_description,
 f.description AS failure_description,g.description AS gate_description,
 CASE WHEN c.obligation_id IS NULL THEN 'OUT_OF_SCOPE'
 WHEN EXISTS(SELECT 1 FROM assertions a JOIN test_results t ON t.assertion_id=a.id
  JOIN test_runs r ON r.id=t.run_id WHERE a.obligation_id=c.obligation_id AND r.historical=0
  AND r.source_seal=(SELECT value FROM metadata WHERE key='source_seal') AND t.status IN ('FAIL','ERROR')) THEN 'FAILED'
 WHEN (SELECT COUNT(DISTINCT a.framework) FROM assertions a JOIN test_results t ON t.assertion_id=a.id
  JOIN test_runs r ON r.id=t.run_id WHERE a.obligation_id=c.obligation_id AND t.status='PASS'
  AND r.historical=0 AND r.source_seal=(SELECT value FROM metadata WHERE key='source_seal')
  AND a.framework IN ('pytest','unittest'))=2 THEN 'SATISFIED'
 ELSE 'OPEN' END AS disposition
FROM cross_findings c JOIN invariants i ON i.id=c.invariant_id
 JOIN failure_modes f ON f.id=c.failure_id JOIN gates g ON g.id=c.gate_id;
CREATE VIEW requirement_evidence AS
SELECT a.requirement_id,a.id AS assertion_id,a.obligation_id,a.test_selector,a.framework,a.categories,
 t.run_id,t.status,t.detail,r.historical,r.source_seal,r.evidence_path
FROM assertions a LEFT JOIN test_results t ON t.assertion_id=a.id LEFT JOIN test_runs r ON r.id=t.run_id;
CREATE VIEW finding_evidence AS
SELECT c.id AS finding_id,c.disposition,c.invariant_id,c.failure_id,c.gate_id,
 c.requirement_id AS invariant_requirement_id,
 CASE WHEN c.obligation_id IS NULL THEN 'CATALOG_SCOPE_AUDIT' ELSE 'BEHAVIORAL_CONTRACT' END AS evidence_kind,e.*
FROM cross_report c LEFT JOIN requirement_evidence e ON e.obligation_id=c.obligation_id
 OR (c.obligation_id IS NULL AND e.assertion_id IN ('SCOPE-PY','SCOPE-UT'));
