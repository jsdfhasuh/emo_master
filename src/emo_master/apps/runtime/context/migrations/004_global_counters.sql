CREATE TABLE IF NOT EXISTS globalCounters (
  projectId TEXT NOT NULL,
  name TEXT NOT NULL,
  value INTEGER NOT NULL CHECK(value >= 0 AND value <= 9223372036854775807),
  updatedAtMs INTEGER NOT NULL,
  PRIMARY KEY (projectId, name)
);
