#!/bin/sh
# Development bootstrap only.  The postgres image runs this once for a new
# volume, using the admin role solely to create isolated service roles/DBs.
set -eu

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  --set goal_intelligence_password="$GOAL_INTELLIGENCE_DB_PASSWORD" \
  --set goal_management_password="$GOAL_MANAGEMENT_DB_PASSWORD" \
  --set discovery_monitoring_password="$DISCOVERY_MONITORING_DB_PASSWORD" \
  --set data_evidence_password="$DATA_EVIDENCE_DB_PASSWORD" \
  --set goal_evaluation_password="$GOAL_EVALUATION_DB_PASSWORD" \
  --set notification_password="$NOTIFICATION_DB_PASSWORD" \
  --set agent_integration_password="$AGENT_INTEGRATION_DB_PASSWORD" <<'SQL'
CREATE ROLE goal_intelligence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'goal_intelligence_password';
CREATE ROLE goal_management LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'goal_management_password';
CREATE ROLE discovery_monitoring LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'discovery_monitoring_password';
CREATE ROLE data_evidence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'data_evidence_password';
CREATE ROLE goal_evaluation LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'goal_evaluation_password';
CREATE ROLE notification LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'notification_password';
CREATE ROLE agent_integration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'agent_integration_password';

CREATE DATABASE goal_intelligence OWNER goal_intelligence;
CREATE DATABASE goal_management OWNER goal_management;
CREATE DATABASE discovery_monitoring OWNER discovery_monitoring;
CREATE DATABASE data_evidence OWNER data_evidence;
CREATE DATABASE goal_evaluation OWNER goal_evaluation;
CREATE DATABASE notification OWNER notification;
CREATE DATABASE agent_integration OWNER agent_integration;

REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE goal_intelligence FROM PUBLIC;
REVOKE CONNECT ON DATABASE goal_management FROM PUBLIC;
REVOKE CONNECT ON DATABASE discovery_monitoring FROM PUBLIC;
REVOKE CONNECT ON DATABASE data_evidence FROM PUBLIC;
REVOKE CONNECT ON DATABASE goal_evaluation FROM PUBLIC;
REVOKE CONNECT ON DATABASE notification FROM PUBLIC;
REVOKE CONNECT ON DATABASE agent_integration FROM PUBLIC;

GRANT CONNECT ON DATABASE goal_intelligence TO goal_intelligence;
GRANT CONNECT ON DATABASE goal_management TO goal_management;
GRANT CONNECT ON DATABASE discovery_monitoring TO discovery_monitoring;
GRANT CONNECT ON DATABASE data_evidence TO data_evidence;
GRANT CONNECT ON DATABASE goal_evaluation TO goal_evaluation;
GRANT CONNECT ON DATABASE notification TO notification;
GRANT CONNECT ON DATABASE agent_integration TO agent_integration;

\connect goal_intelligence
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO goal_intelligence;
\connect goal_management
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO goal_management;
\connect discovery_monitoring
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO discovery_monitoring;
\connect data_evidence
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO data_evidence;
\connect goal_evaluation
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO goal_evaluation;
\connect notification
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO notification;
\connect agent_integration
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO agent_integration;
SQL
