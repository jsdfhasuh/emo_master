ALTER TABLE jobEvents ADD COLUMN originApp TEXT NOT NULL DEFAULT 'runtime';
ALTER TABLE jobEvents ADD COLUMN component TEXT NOT NULL DEFAULT 'application';
ALTER TABLE jobEvents ADD COLUMN runtimeInstanceId TEXT NOT NULL DEFAULT '';
UPDATE jobEvents SET component = CASE
  WHEN eventType = 'node.log' THEN 'operator'
  WHEN eventType GLOB 'job.*' OR eventType GLOB 'node.*' OR eventType GLOB 'workflow.*'
    OR eventType GLOB 'scheduler.*' OR eventType GLOB 'dispatch.*' OR eventType GLOB 'loop.*' THEN 'execution'
  WHEN eventType GLOB 'device.*' THEN 'device'
  WHEN eventType GLOB 'presentation.*' THEN 'presentation'
  WHEN eventType GLOB 'sqlite.*' OR eventType GLOB 'runtime.log*'
    OR eventType GLOB 'runtime.event*' OR eventType = 'resource.cleanup.failed'
    OR eventType IN ('runtime.maintenance.failed', 'operator.storage.resolved') THEN 'storage'
  ELSE 'application' END;
