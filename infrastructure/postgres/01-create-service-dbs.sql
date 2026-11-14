-- DEVELOPMENT BOOTSTRAP ONLY: databases are separate, but bootstrap role remains superuser.
-- Before staging/production, create one scoped non-superuser role per service.
CREATE DATABASE goal_intelligence;
CREATE DATABASE goal_management;
CREATE DATABASE discovery_monitoring;
CREATE DATABASE data_evidence;
CREATE DATABASE goal_evaluation;
CREATE DATABASE notification;
CREATE DATABASE agent_integration;
