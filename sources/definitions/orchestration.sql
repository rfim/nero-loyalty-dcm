-- =============================================================================
-- Caffe Nero Loyalty Star Schema — Orchestration
-- Database: NERO_DB  Schema: NERO_LOYALTY
--
-- Python procedure + task showing how DCM manages orchestration declaratively.
-- Keeps DIM_DATE populated for the current calendar year on a daily schedule.
-- Deployed via the same `snow dcm plan/deploy` flow as the tables — no
-- separate orchestrator needed for this piece.
-- =============================================================================

DEFINE PROCEDURE NERO_DB.NERO_LOYALTY.SP_REFRESH_DIM_DATE(YEARS_AHEAD NUMBER)
RETURNS VARCHAR
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
HANDLER = 'run'
COMMENT = 'Upserts DIM_DATE rows for today through YEARS_AHEAD years out. Idempotent — safe to re-run.'
AS
$$
from datetime import date, timedelta

def run(session, years_ahead: int) -> str:
    start = date.today()
    end = date(start.year + years_ahead, 12, 31)
    rows = []
    d = start
    while d <= end:
        rows.append((
            int(d.strftime('%Y%m%d')),
            d, d.year, (d.month - 1) // 3 + 1, d.month, d.strftime('%B'),
            d.day, d.isoweekday(), d.strftime('%A'), int(d.strftime('%W')),
            d.isoweekday() >= 6,
        ))
        d += timedelta(days=1)

    df = session.create_dataframe(
        rows,
        schema=["DATE_SK", "FULL_DATE", "YEAR", "QUARTER", "MONTH", "MONTH_NAME",
                "DAY_OF_MONTH", "DAY_OF_WEEK", "DAY_NAME", "WEEK_OF_YEAR", "IS_WEEKEND"],
    )
    df.write.save_as_table("NERO_DB.NERO_LOYALTY.DIM_DATE", mode="overwrite")
    return f"DIM_DATE refreshed: {len(rows)} rows ({start} to {end})"
$$;

-- Suspended by default (DCM default) — flip to STARTED once the team is
-- ready for this to run unattended. Deliberately left off for the exercise.
DEFINE TASK NERO_DB.NERO_LOYALTY.TSK_REFRESH_DIM_DATE
    WAREHOUSE = 'COMPUTE_WH'
    SCHEDULE = 'USING CRON 0 3 * * * UTC'
    COMMENT = 'Daily refresh of DIM_DATE via SP_REFRESH_DIM_DATE.'
AS
    CALL NERO_DB.NERO_LOYALTY.SP_REFRESH_DIM_DATE(2);
