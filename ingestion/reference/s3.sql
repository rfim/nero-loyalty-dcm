-- UNVERIFIED — reference only, not deployed by DCM (lives outside sources/definitions).
-- No live AWS credentials in this account to test this route. Replace the
-- REPLACE_* placeholders and move into sources/definitions/ once validated.
--
-- Under landing.type=typed (per-dataset bronze tables, not one shared
-- envelope table), a single auto-ingest PIPE can no longer target 'the
-- landing table' generically -- each dataset's CSV would need its own
-- pipe (one COPY INTO per BRONZE_<DATASET>, keyed by a file naming
-- convention like <dataset>/<batch_id>.csv) plus a separate insert into
-- BRONZE_BATCH_MANIFESTS once all of a batch's files have landed. That's
-- a real design, not sketched here since there's no live S3 bucket to
-- validate it against -- left as the next step if a real S3 source is
-- ever added.

CREATE STORAGE INTEGRATION IF NOT EXISTS NERO_S3_INTEGRATION
    TYPE = EXTERNAL_STAGE
    STORAGE_PROVIDER = 'S3'
    ENABLED = TRUE
    STORAGE_AWS_ROLE_ARN = 'arn:aws:iam::123456789012:role/REPLACE_ROLE'
    STORAGE_ALLOWED_LOCATIONS = ('s3://REPLACE_BUCKET/nero/');

CREATE STAGE IF NOT EXISTS NERO_DB."00_BRONZE".S3_STAGE
    URL = 's3://REPLACE_BUCKET/nero/'
    STORAGE_INTEGRATION = NERO_S3_INTEGRATION;
