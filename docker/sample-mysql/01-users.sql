-- erp_db sample source: the read-only user a customer creates (docs/05), and a writer for the
-- refusal test (M2). Locally the host is '%'; customers restrict it to the egress range.
CREATE USER 'othor_ro'@'%' IDENTIFIED BY 'othor_ro_dev';
GRANT SELECT, SHOW VIEW ON erp_db.* TO 'othor_ro'@'%';
CREATE USER 'othor_rw'@'%' IDENTIFIED BY 'othor_rw_dev';
GRANT SELECT, INSERT, UPDATE, DELETE ON erp_db.* TO 'othor_rw'@'%';
FLUSH PRIVILEGES;
