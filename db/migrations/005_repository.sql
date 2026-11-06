-- Prepared finance files. These rows are the supplied workbooks and decks, not ledger facts.
BEGIN;

CREATE TABLE finance.repository_node (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  parent_id uuid REFERENCES finance.repository_node(id) ON DELETE CASCADE,
  name text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('folder', 'file')),
  mime text,
  size_bytes bigint,
  sort_index integer NOT NULL CHECK (sort_index > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT repository_node_name_ok CHECK (
    char_length(btrim(name)) BETWEEN 1 AND 240
    AND name = btrim(name)
    AND name <> '.'
    AND name <> '..'
    AND position('/' IN name) = 0
    AND position(E'\\' IN name) = 0
    AND name !~ '[[:cntrl:]]'
  ),
  CONSTRAINT repository_node_kind_shape CHECK (
    (kind = 'folder' AND mime IS NULL AND size_bytes IS NULL)
    OR (kind = 'file' AND mime IS NOT NULL AND size_bytes IS NOT NULL AND size_bytes >= 0 AND size_bytes <= 52428800)
  )
);

CREATE UNIQUE INDEX repository_node_sibling_name
  ON finance.repository_node ((COALESCE(parent_id, '00000000-0000-0000-0000-000000000000'::uuid)), (lower(name)));

CREATE INDEX repository_node_parent ON finance.repository_node (parent_id);

CREATE TABLE finance.repository_file (
  node_id uuid PRIMARY KEY REFERENCES finance.repository_node(id) ON DELETE CASCADE,
  content bytea NOT NULL
);

ALTER TABLE finance.repository_node ENABLE ROW LEVEL SECURITY;
ALTER TABLE finance.repository_file ENABLE ROW LEVEL SECURITY;

CREATE POLICY repository_node_app ON finance.repository_node
  FOR ALL TO vggos_app
  USING (true)
  WITH CHECK (true);

CREATE POLICY repository_file_app ON finance.repository_file
  FOR ALL TO vggos_app
  USING (true)
  WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON finance.repository_node, finance.repository_file TO vggos_app;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'REVOKE ALL ON TABLE finance.repository_node FROM anon';
    EXECUTE 'REVOKE ALL ON TABLE finance.repository_file FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'REVOKE ALL ON TABLE finance.repository_node FROM authenticated';
    EXECUTE 'REVOKE ALL ON TABLE finance.repository_file FROM authenticated';
  END IF;
END $$;

INSERT INTO core.app_migration (version) VALUES ('005_repository');

COMMIT;
