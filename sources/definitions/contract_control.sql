-- =============================================================================
-- Data Contract Ingestion Pattern — Control Plane
-- Database: NERO_DB  Schema: NERO_LOYALTY
--
-- Scoped-down implementation of the credential-free "internal_stage + native"
-- route from the ingestion guide. Tables are prefixed (CONTROL_/RAW_/WORK_/
-- VALIDATED_) rather than split into separate schemas, since a DCM project
-- can only define objects inside its own schema. Skips the YAML-driven
-- generator, S3/Snowpipe and Fivetran routes as out-of-scope extensions.
-- =============================================================================

DEFINE TABLE NERO_DB.NERO_LOYALTY.CONTROL_DATA_CONTRACTS (
    CONTRACT_ID    VARCHAR(200)   NOT NULL,
    VERSION        NUMBER         NOT NULL,
    CONTRACT_JSON  VARIANT        NOT NULL,
    CONTRACT_HASH  VARCHAR(64)    NOT NULL,
    LOADED_AT      TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (CONTRACT_ID, VERSION)
)
COMMENT = 'Reviewed data contract documents, keyed by contract_id+version. CONTRACT_HASH is the SHA-256 of the exact contract bytes, pinned by the stored procedures at deploy time.';

DEFINE TABLE NERO_DB.NERO_LOYALTY.CONTROL_RUN_AUDIT (
    BATCH_ID     VARCHAR(200)   NOT NULL,
    RUN_ID       VARCHAR(200)   NOT NULL,
    STATUS       VARCHAR(30)    NOT NULL,
    DETAILS      VARIANT,
    RECORDED_AT  TIMESTAMP_TZ   DEFAULT CURRENT_TIMESTAMP()
)
COMMENT = 'Audit trail of every PROCESS_BATCH invocation. STATUS one of: PUBLISHED, ALREADY_PUBLISHED, SUPERSEDED, REJECTED, INCOMPLETE, ERROR.';

DEFINE TABLE NERO_DB.NERO_LOYALTY.CONTROL_RELEASE_POINTER (
    POINTER_NAME       VARCHAR(50)   NOT NULL,
    CURRENT_BATCH_ID   VARCHAR(200),
    CURRENT_RELEASE_AT TIMESTAMP_TZ,
    UPDATED_AT         TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (POINTER_NAME)
)
COMMENT = 'Compare-and-set pointer to the currently published batch. Reporting views join on this to expose only the approved release.';
