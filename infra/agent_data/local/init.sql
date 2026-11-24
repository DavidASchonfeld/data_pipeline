-- Runs once, the first time the local pgvector container starts with an empty volume.
-- Turns on the vector extension; the search tables themselves arrive with the search-index migration.
CREATE EXTENSION IF NOT EXISTS vector;
