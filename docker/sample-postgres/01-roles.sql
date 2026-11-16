-- sales_dw sample source: two schemas and the roles Othor AI would be given.
-- othor_ro is what a customer creates (docs/05); othor_rw is a writer, used to prove refusal (M2).
CREATE SCHEMA IF NOT EXISTS sales_dw AUTHORIZATION dw_admin;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE ROLE othor_ro LOGIN PASSWORD 'othor_ro_dev';
GRANT CONNECT ON DATABASE sales_dw TO othor_ro;
GRANT USAGE ON SCHEMA public, sales_dw TO othor_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE dw_admin IN SCHEMA public, sales_dw GRANT SELECT ON TABLES TO othor_ro;

CREATE ROLE othor_rw LOGIN PASSWORD 'othor_rw_dev';
GRANT CONNECT ON DATABASE sales_dw TO othor_rw;
GRANT USAGE ON SCHEMA public, sales_dw TO othor_rw;
ALTER DEFAULT PRIVILEGES FOR ROLE dw_admin IN SCHEMA public, sales_dw
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO othor_rw;
