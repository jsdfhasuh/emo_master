# CI migration staging
These adapters do not create a default-branch workflow, consume GitHub Secrets,
write required statuses, publish releases, or alter the 12 legacy workflows.
The controller is responsible for verifying source/ref and starting an isolated
worker. Redirection and variable filtering do not constitute worker isolation.
Raw evidence stays local unless there is an independently vetted encrypted
artifact path. Availability of an adapter is not CI execution evidence.
