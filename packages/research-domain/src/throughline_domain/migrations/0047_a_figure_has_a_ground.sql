-- An exported figure is drawn on a ground: light, dark, or none at all.
--
-- Every publication export was opaque white, with its greys written into the
-- renderer. A figure dropped on a dark slide or a dark README was a white box,
-- and there was no way to ask for anything else. The renderer now draws from
-- the shared tokens on a light or a dark ground, optionally transparent — and
-- the same figure on two grounds is two files, not one file overwritten by
-- whichever was asked for last. So the ground joins the render's identity.
--
-- Existing rows are all light and opaque, which is what the defaults say.

ALTER TABLE visual_renders
    ADD COLUMN IF NOT EXISTS ground      TEXT    NOT NULL DEFAULT 'light',
    ADD COLUMN IF NOT EXISTS transparent BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN visual_renders.ground IS
    'The neutrals the figure is drawn in: light or dark (throughline_visual.tokens).';
COMMENT ON COLUMN visual_renders.transparent IS
    'Whether nothing is painted behind the marks, so the figure takes the page it sits on.';

DROP INDEX IF EXISTS idx_visual_renders_identity;
CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_renders_identity
    ON visual_renders (visual_id, format, spec_hash, COALESCE(height_px, -1),
                       renderer, ground, transparent);
