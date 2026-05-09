-- Crea la base de datos de tests si no existe
SELECT 'CREATE DATABASE chatpro_test OWNER chatpro'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'chatpro_test')\gexec

-- Habilita extensión pgvector en ambas bases de datos
\c chatpro
CREATE EXTENSION IF NOT EXISTS vector;

\c chatpro_test
CREATE EXTENSION IF NOT EXISTS vector;
