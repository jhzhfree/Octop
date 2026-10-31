-- Schema v29: which MCP tool an external source calls, and what it is called with.
ALTER TABLE external_knowledge_sources ADD COLUMN tool_name TEXT NOT NULL DEFAULT '';
ALTER TABLE external_knowledge_sources ADD COLUMN tool_arguments TEXT NOT NULL DEFAULT '';
UPDATE _schema_version SET version = 29;
