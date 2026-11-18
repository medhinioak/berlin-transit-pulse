-- Schemas and roles: one schema per writing service, owned by that service's role.
-- Runs once, when the Postgres volume is empty. Passwords come from the container env.

\set ON_ERROR_STOP on
\getenv processor_password BTP_PG_PROCESSOR_PASSWORD
\getenv schedule_password BTP_PG_SCHEDULE_PASSWORD
\getenv metrics_jobs_password BTP_PG_METRICS_JOBS_PASSWORD
\getenv api_password BTP_PG_API_PASSWORD

CREATE ROLE btp_processor LOGIN PASSWORD :'processor_password';
CREATE ROLE btp_schedule LOGIN PASSWORD :'schedule_password';
CREATE ROLE btp_metrics_jobs LOGIN PASSWORD :'metrics_jobs_password';
CREATE ROLE btp_api LOGIN PASSWORD :'api_password';

-- Nobody but the superuser creates objects in public or connects without a grant.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE btp FROM PUBLIC;
GRANT CONNECT ON DATABASE btp TO btp_processor, btp_schedule, btp_metrics_jobs, btp_api;

-- Owning its schema gives a role USAGE and CREATE there and nowhere else.
CREATE SCHEMA events AUTHORIZATION btp_processor;
CREATE SCHEMA schedule AUTHORIZATION btp_schedule;
CREATE SCHEMA marts AUTHORIZATION btp_metrics_jobs;

-- metrics-jobs reads events and schedule; the API reads marts only.
GRANT USAGE ON SCHEMA events, schedule TO btp_metrics_jobs;
GRANT USAGE ON SCHEMA marts TO btp_api;

-- Tables and views each owner creates later are readable by its readers.
ALTER DEFAULT PRIVILEGES FOR ROLE btp_processor IN SCHEMA events
    GRANT SELECT ON TABLES TO btp_metrics_jobs;
ALTER DEFAULT PRIVILEGES FOR ROLE btp_schedule IN SCHEMA schedule
    GRANT SELECT ON TABLES TO btp_metrics_jobs;
ALTER DEFAULT PRIVILEGES FOR ROLE btp_metrics_jobs IN SCHEMA marts
    GRANT SELECT ON TABLES TO btp_api;
