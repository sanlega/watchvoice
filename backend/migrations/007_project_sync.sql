-- 007: each runner reports its T3 projects (POST /v1/runners/{name}/projects); availability per machine. Additive.
BEGIN;
CREATE TABLE project_runners (
 project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 runner_id uuid NOT NULL REFERENCES runners(id) ON DELETE CASCADE,
 folder text NOT NULL, -- path on that machine; never logged
 has_git boolean NOT NULL,
 has_origin boolean NOT NULL,
 seen_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (project_id, runner_id)
);
CREATE INDEX project_runners_runner ON project_runners(runner_id);
COMMIT;
