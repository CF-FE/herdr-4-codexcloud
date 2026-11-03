-- Synthetic ledger from scripts/project_coordinator.py at 6252cf39fffd08b80d82ee1150a9b56e895fa479
-- Registered project, accepted request, waiting report v1 and its exact read receipt.
BEGIN TRANSACTION;
CREATE TABLE assignments(
 project_id TEXT NOT NULL REFERENCES projects, generation INTEGER NOT NULL, proof TEXT NOT NULL,
 PRIMARY KEY(project_id,generation));
CREATE TABLE contacts(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, action_id TEXT NOT NULL UNIQUE,
 generation INTEGER NOT NULL, owner TEXT NOT NULL, endpoint TEXT NOT NULL,
 state TEXT NOT NULL, transport_ref TEXT, read_ref TEXT, reply_hash TEXT, reason TEXT,
 PRIMARY KEY(project_id,request_id), FOREIGN KEY(project_id,request_id) REFERENCES requests);
CREATE TABLE creations(
 project_id TEXT NOT NULL REFERENCES projects, creation_id TEXT NOT NULL, generation INTEGER NOT NULL,
 input_hash TEXT NOT NULL, state TEXT NOT NULL, candidate_ref TEXT, evidence_ref TEXT,
 PRIMARY KEY(project_id,creation_id));
CREATE TABLE events(
 seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects, request_id TEXT,
 kind TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL, body TEXT NOT NULL,
 event_key TEXT NOT NULL UNIQUE);
INSERT INTO "events" VALUES(1,'alpha',NULL,'PROJECT_REGISTERED','local:administrator','2030-01-01T00:00:00+00:00','{"g8_principal":"g8:alpha","project_id":"alpha","project_ref":"project:alpha","report_route":"g8-route:alpha","repository":"CF-FE/alpha"}','5dc712f589d49b7867d58d238f64111b31c7a4128ae4fd27e6486505ffa03a39');
INSERT INTO "events" VALUES(2,'alpha','query-1','REQUEST_ACCEPTED','g8:alpha','2030-01-01T00:00:00+00:00','{"input_hash":"87aa6a72caa063f9d0872d7c8f3147337897342f30ada0ee372fd519bd17adee"}','112ce8622b137228e2150b7ad45d9ff433c79e809b1b1c40162ee4310df99a62');
INSERT INTO "events" VALUES(3,'alpha','query-1','RESULT_AVAILABLE','g8-project-coordinator','2030-01-01T00:00:00+00:00','{"result_hash":"7bc2ca611e4aa09e1aa751ecaa7806c9f2ff5ffc04004212e8107c8fff9815ec","version":1}','5ca9dcaf44f5ccbf0dc125cec1bd8f294f5dbdc2b8e35c805dab0adb3b78c7b2');
INSERT INTO "events" VALUES(4,'alpha','query-1','G8_READ','g8:alpha','2030-01-01T00:00:00+00:00','{"evidence_ref":"native:original-read","result_hash":"7bc2ca611e4aa09e1aa751ecaa7806c9f2ff5ffc04004212e8107c8fff9815ec","version":1}','8d7366e973824f3e93be047aa47c6709d3a9df85a9a8b45c44ed8244b87254b5');
CREATE TABLE notifications(
 seq INTEGER PRIMARY KEY REFERENCES events, project_id TEXT NOT NULL, request_id TEXT,
 recipient TEXT NOT NULL);
INSERT INTO "notifications" VALUES(2,'alpha','query-1','g8:alpha');
INSERT INTO "notifications" VALUES(3,'alpha','query-1','g8:alpha');
CREATE TABLE projects(
 project_id TEXT PRIMARY KEY, repository TEXT NOT NULL, project_ref TEXT NOT NULL,
 g8_principal TEXT NOT NULL, report_route TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
 owner_principal TEXT, endpoint TEXT, assignment_state TEXT NOT NULL DEFAULT 'UNVERIFIED');
INSERT INTO "projects" VALUES('alpha','CF-FE/alpha','project:alpha','g8:alpha','g8-route:alpha',0,NULL,NULL,'UNVERIFIED');
CREATE TABLE requests(
 project_id TEXT NOT NULL REFERENCES projects, request_id TEXT NOT NULL, principal TEXT NOT NULL,
 kind TEXT NOT NULL, idem TEXT NOT NULL, input_hash TEXT NOT NULL, body TEXT NOT NULL,
 target_generation INTEGER NOT NULL, created_at TEXT NOT NULL, waiting TEXT NOT NULL,
 cancel_ref TEXT, unblock_ref TEXT,
 PRIMARY KEY(project_id,request_id), UNIQUE(project_id,principal,kind,idem));
INSERT INTO "requests" VALUES('alpha','query-1','g8:alpha','QUERY_BLOCKER','original-query','87aa6a72caa063f9d0872d7c8f3147337897342f30ada0ee372fd519bd17adee','{"authorization_ref":"read-only-authority","budget":{"audio_generations":0,"contact_attempts":1,"production_writes":0},"deadline":"2030-01-02T00:00:00Z","evidence_refs":["batch-ref:v1"],"expected_generation":0,"idempotency_key":"original-query","job_id":"job-alpha","kind":"QUERY_BLOCKER","payload_hash":"ddda7d9abd1c26aade5476e982f201b4cc4071548588cd97f92c40973de67f04","project_id":"alpha","question":"Which input batch is blocked?","request_id":"query-1","task_ref":"authority:alpha"}',0,'2030-01-01T00:00:00+00:00','{"action_owner":"g8-project-coordinator","deadline":"2030-01-02T00:00:00Z","escalate_to":"g8:alpha","evidence_ref":"authority:alpha","follow_up_owner":"g8-project-coordinator","next_condition":"Verified project owner and approved contact route","reason":"VERIFY_APPOINTMENT"}',NULL,NULL);
CREATE TABLE results(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, version INTEGER NOT NULL,
 result_hash TEXT NOT NULL, body TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, read_ref TEXT,
 PRIMARY KEY(project_id,request_id,version), UNIQUE(project_id,request_id,result_hash),
 FOREIGN KEY(project_id,request_id) REFERENCES requests);
INSERT INTO "results" VALUES('alpha','query-1',1,'7bc2ca611e4aa09e1aa751ecaa7806c9f2ff5ffc04004212e8107c8fff9815ec','{"job_id":"job-alpha","kind":"COORDINATOR_WAIT_REPORT","payload_hash":"ddda7d9abd1c26aade5476e982f201b4cc4071548588cd97f92c40973de67f04","project_id":"alpha","request_id":"query-1","schema_version":1,"source_evidence_ref":"original:wait","source_principal":"g8-project-coordinator","task_ref":"authority:alpha","waiting":{"action_owner":"g8-project-coordinator","deadline":"2030-01-02T00:00:00Z","escalate_to":"g8:alpha","evidence_ref":"authority:alpha","follow_up_owner":"g8-project-coordinator","next_condition":"Verified project owner and approved contact route","reason":"VERIFY_APPOINTMENT"}}','coordinate-229f7f2dd15672f66070d09ffda25fd006f0b5bdaddb0bd840027ab11470f437','native:original-read');
CREATE TABLE wait_schedule(
 project_id TEXT NOT NULL, request_id TEXT NOT NULL, revision INTEGER NOT NULL, material_hash TEXT NOT NULL,
 due TEXT, phase TEXT NOT NULL, deadline TEXT NOT NULL, PRIMARY KEY(project_id,request_id),
 FOREIGN KEY(project_id,request_id) REFERENCES requests);
INSERT INTO "wait_schedule" VALUES('alpha','query-1',1,'61153c8e62ffe37033eb236ba92b73ef4aa8f5ea8371c76ad404686f322e7795','2030-01-02T00:00:00+00:00','DEADLINE','2030-01-02T00:00:00+00:00');
CREATE UNIQUE INDEX open_creation ON creations(project_id)
 WHERE state IN ('INTENT','UNKNOWN','CREATED');
CREATE INDEX coordinator_due ON wait_schedule(due,project_id,request_id) WHERE due IS NOT NULL;
CREATE INDEX project_due ON wait_schedule(project_id,due,request_id) WHERE due IS NOT NULL;
CREATE INDEX project_event_kind ON events(project_id,kind,seq);
COMMIT;
