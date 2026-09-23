-- Lineage edges may only connect research objects inside the edge's project.
--
-- Application code already enforces this, but provenance is important enough
-- that a direct SQL writer or future caller must not be able to manufacture a
-- cross-project graph behind that check.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM artifact_lineage_edges e
          JOIN research_objects s ON s.id = e.source_artifact_id
          JOIN research_objects t ON t.id = e.target_artifact_id
         WHERE s.project_id <> e.project_id
            OR t.project_id <> e.project_id
    ) THEN
        RAISE EXCEPTION
            'existing cross-project lineage edges must be repaired before migration 0048';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION enforce_lineage_edge_project()
RETURNS trigger AS $$
DECLARE
    source_project TEXT;
    target_project TEXT;
BEGIN
    SELECT project_id INTO source_project
      FROM research_objects
     WHERE id = NEW.source_artifact_id;

    SELECT project_id INTO target_project
      FROM research_objects
     WHERE id = NEW.target_artifact_id;

    IF source_project IS NULL OR target_project IS NULL
       OR source_project <> NEW.project_id
       OR target_project <> NEW.project_id THEN
        RAISE EXCEPTION
            'lineage endpoints must belong to edge project %', NEW.project_id;
    END IF;

    RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lineage_edge_project_guard ON artifact_lineage_edges;
CREATE TRIGGER lineage_edge_project_guard
    BEFORE INSERT OR UPDATE OF project_id, source_artifact_id, target_artifact_id
    ON artifact_lineage_edges
    FOR EACH ROW EXECUTE FUNCTION enforce_lineage_edge_project();
