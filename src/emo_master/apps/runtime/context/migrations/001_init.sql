CREATE TABLE IF NOT EXISTS projects (
  projectId TEXT NOT NULL,
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  path TEXT NOT NULL,
  createdAt TEXT NOT NULL,
  updatedAt TEXT NOT NULL,
  PRIMARY KEY (projectId, version)
);

CREATE TABLE IF NOT EXISTS projectReleases (
  releaseId TEXT PRIMARY KEY,
  projectId TEXT NOT NULL,
  packagePath TEXT NOT NULL,
  checksum TEXT NOT NULL,
  runtimeMin TEXT NOT NULL,
  runtimeMax TEXT NOT NULL,
  createdAt TEXT NOT NULL,
  isRollbackPoint INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  jobId TEXT PRIMARY KEY,
  projectId TEXT NOT NULL,
  releaseId TEXT NOT NULL,
  status TEXT NOT NULL,
  startAt TEXT NOT NULL,
  endAt TEXT,
  durationMs REAL,
  errorCode TEXT
);

CREATE TABLE IF NOT EXISTS jobEvents (
  eventId INTEGER PRIMARY KEY AUTOINCREMENT,
  jobId TEXT NOT NULL,
  nodeId TEXT NOT NULL,
  eventType TEXT NOT NULL,
  level TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  payloadJson TEXT NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pluginDiagnostics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  operatorId TEXT NOT NULL,
  version TEXT NOT NULL,
  status TEXT NOT NULL,
  reasonCode TEXT NOT NULL,
  reasonMessage TEXT NOT NULL,
  timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deviceBindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  projectId TEXT NOT NULL,
  logicalName TEXT NOT NULL,
  deviceType TEXT NOT NULL,
  configJson TEXT NOT NULL,
  updatedAt TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_startAt ON jobs(status, startAt);
CREATE INDEX IF NOT EXISTS idx_jobEvents_jobId_timestamp ON jobEvents(jobId, timestamp);
CREATE INDEX IF NOT EXISTS idx_jobEvents_eventType_timestamp ON jobEvents(eventType, timestamp);
