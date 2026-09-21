"""Loyalty Engagement & Sales — native Streamlit-in-Snowflake dashboard.

Built for Part 4 of the exercise brief (store operations / marketing
stakeholder), and structured section-by-section to answer its 3 questions
directly: (1) which stores/regions lead or lag on loyalty engagement and
whether that's changed recently, (2) how loyalty tier relates to basket
size and visit frequency, (3) anything else in the data worth a
stakeholder's attention.

Queries NERO_DB."01_BRONZE" (the dbt-built, contract-filtered layer —
renamed from "01_SILVER"/VALIDATED_* mid-project, see analytics/README.md)
rather than the gold/SCD layer — tier is read as each customer's *current*
tier, not reconstructed at each historical transaction, because the
source has no tier-change history to do that precisely.

Two metric definitions were confirmed by email with the stakeholder
(Matt Greenwell, Caffe Nero, 2026-09-18) rather than assumed:
- Store/region signups are attributed to the store recorded on the
  signup event itself, not the customer's home store -- "sign ups by
  store then yes [event store]... [customer] home store then no." The
  two differ whenever a signup happens away from the store someone ends
  up transacting at most.
- Redemption rate is "Number redeemed / Number issued" -- confirmed as a
  rate, not a raw count, with "issued" mapped to `earn` events, the only
  proxy this dataset has for a reward being made available to redeem.
Customer tier (current, not historical) and loyalty visits
(customer_id present = identified) were also confirmed, unchanged from
the original assumption.

The visual design (palette, type, chart drawing, hover tooltips) is kept
byte-for-byte identical to the Claude Artifact version of this dashboard —
only the data source changed, from a static JSON snapshot to a live query
against Snowflake. dashboard_template.html holds that markup/CSS/JS with a
single __DATA_JSON__ placeholder this file fills at render time.
"""
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from snowflake.snowpark.context import get_active_session

import sys as _sys
from pathlib import Path as _Path
_p = _Path(__file__).resolve().parent
while not (_p / "shared").is_dir() and _p != _p.parent:
    _p = _p.parent
_sys.path.insert(0, str(_p / "shared"))

import branding

st.set_page_config(page_title="Loyalty Engagement & Sales", layout="wide")

session = get_active_session()
BRONZE = 'NERO_DB."01_BRONZE"'


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


@st.cache_data(ttl=300)
def load_dashboard_data():
    summary = one(f"""
        SELECT
          (SELECT COUNT(*) FROM {BRONZE}.BRONZE_STORES)              AS N_STORES,
          (SELECT COUNT(*) FROM {BRONZE}.BRONZE_LOYALTY_CUSTOMERS)   AS N_CUSTOMERS,
          (SELECT COUNT(*) FROM {BRONZE}.BRONZE_TRANSACTIONS)        AS N_TRANSACTIONS,
          (SELECT COUNT(*) FROM {BRONZE}.BRONZE_LOYALTY_EVENTS)      AS N_EVENTS,
          (SELECT MIN(TRANSACTION_TS) FROM {BRONZE}.BRONZE_TRANSACTIONS) AS TXN_DATE_MIN,
          (SELECT MAX(TRANSACTION_TS) FROM {BRONZE}.BRONZE_TRANSACTIONS) AS TXN_DATE_MAX,
          (SELECT MIN(EVENT_TS) FROM {BRONZE}.BRONZE_LOYALTY_EVENTS)     AS EVENT_DATE_MIN,
          (SELECT MAX(EVENT_TS) FROM {BRONZE}.BRONZE_LOYALTY_EVENTS)     AS EVENT_DATE_MAX,
          (SELECT COUNT(*) FROM {BRONZE}.BRONZE_LOYALTY_CUSTOMERS)   AS TOTAL_SIGNUPS,
          (SELECT COUNT_IF(EVENT_TYPE = 'earn') FROM {BRONZE}.BRONZE_LOYALTY_EVENTS)   AS TOTAL_EARNS,
          (SELECT COUNT_IF(EVENT_TYPE = 'redeem') FROM {BRONZE}.BRONZE_LOYALTY_EVENTS) AS TOTAL_REDEEMS,
          (SELECT DIV0(COUNT_IF(EVENT_TYPE = 'redeem'), COUNT_IF(EVENT_TYPE = 'earn'))
             FROM {BRONZE}.BRONZE_LOYALTY_EVENTS)                    AS OVERALL_RR,
          (SELECT DIV0(COUNT_IF(CUSTOMER_ID IS NOT NULL), COUNT(*))
             FROM {BRONZE}.BRONZE_TRANSACTIONS)                      AS LOYALTY_TXN_SHARE,
          (SELECT COUNT(DISTINCT REWARD_ID) FROM {BRONZE}.BRONZE_LOYALTY_EVENTS
             WHERE REWARD_ID IS NOT NULL)                               AS N_DISTINCT_REWARDS,
          (SELECT AVG(BASKET_TOTAL) FROM {BRONZE}.BRONZE_TRANSACTIONS) AS AVG_BASKET_ALL
    """)

    store_rows = rows(f"""
        WITH bounds AS (SELECT MAX(EVENT_TS) AS max_ts FROM {BRONZE}.BRONZE_LOYALTY_EVENTS),
        ev AS (
          SELECT e.STORE_ID, e.EVENT_TYPE,
            CASE WHEN e.EVENT_TS > DATEADD(day, -28, b.max_ts) THEN 'recent'
                 WHEN e.EVENT_TS > DATEADD(day, -56, b.max_ts) THEN 'prior'
                 ELSE 'older' END AS win
          FROM {BRONZE}.BRONZE_LOYALTY_EVENTS e, bounds b
        ),
        agg AS (
          SELECT STORE_ID,
            COUNT_IF(EVENT_TYPE = 'earn')                          AS earns,
            COUNT_IF(EVENT_TYPE = 'redeem')                        AS redeems,
            COUNT_IF(EVENT_TYPE = 'earn' AND win = 'recent')       AS earns_recent,
            COUNT_IF(EVENT_TYPE = 'redeem' AND win = 'recent')     AS redeems_recent,
            COUNT_IF(EVENT_TYPE = 'earn' AND win = 'prior')        AS earns_prior,
            COUNT_IF(EVENT_TYPE = 'redeem' AND win = 'prior')      AS redeems_prior
          FROM ev GROUP BY STORE_ID
        ),
        signups AS (
          -- Attributed to the store recorded on the signup event itself,
          -- not the customer's home store -- confirmed by email 2026-09-18
          -- (Matt Greenwell, Caffe Nero): "sign ups by store then yes [use
          -- the signup event's store]... [customer] home store then no."
          -- The two differ whenever someone signs up away from the store
          -- they end up transacting at most often.
          SELECT STORE_ID, COUNT(*) AS signups
          FROM {BRONZE}.BRONZE_LOYALTY_EVENTS
          WHERE EVENT_TYPE = 'signup'
          GROUP BY STORE_ID
        )
        SELECT s.STORE_ID AS STORE_ID, s.STORE_NAME AS NAME, s.REGION AS REGION, s.FORMAT AS FORMAT,
          COALESCE(g.signups, 0) AS SIGNUPS,
          COALESCE(a.earns, 0)   AS EARNS,
          COALESCE(a.redeems, 0) AS REDEEMS,
          DIV0(a.redeems, a.earns)               AS RR,
          DIV0(a.redeems_recent, a.earns_recent)  AS RR_RECENT,
          DIV0(a.redeems_prior, a.earns_prior)    AS RR_PRIOR
        FROM {BRONZE}.BRONZE_STORES s
        LEFT JOIN agg a ON a.STORE_ID = s.STORE_ID
        LEFT JOIN signups g ON g.STORE_ID = s.STORE_ID
        ORDER BY RR DESC
    """)
    for r in store_rows:
        r["RR_DELTA"] = r["RR_RECENT"] - r["RR_PRIOR"]

    region_rows = rows(f"""
        WITH signups AS (
          -- Same store-attribution fix as store_rows above: signup
          -- event's own store, not customer home store.
          SELECT s.REGION, COUNT(*) AS signups
          FROM {BRONZE}.BRONZE_LOYALTY_EVENTS e
          JOIN {BRONZE}.BRONZE_STORES s ON s.STORE_ID = e.STORE_ID
          WHERE e.EVENT_TYPE = 'signup'
          GROUP BY s.REGION
        ),
        rr AS (
          SELECT s.REGION,
            DIV0(COUNT_IF(e.EVENT_TYPE = 'redeem'), COUNT_IF(e.EVENT_TYPE = 'earn')) AS rr
          FROM {BRONZE}.BRONZE_LOYALTY_EVENTS e
          JOIN {BRONZE}.BRONZE_STORES s ON s.STORE_ID = e.STORE_ID
          GROUP BY s.REGION
        )
        SELECT g.REGION AS REGION, g.signups AS SIGNUPS, r.rr AS RR
        FROM signups g JOIN rr r ON r.REGION = g.REGION
        ORDER BY g.signups DESC
    """)

    segment_rows = rows(f"""
        WITH bounds AS (
          SELECT DATEDIFF('day', MIN(TRANSACTION_TS), MAX(TRANSACTION_TS)) + 1 AS days
          FROM {BRONZE}.BRONZE_TRANSACTIONS
        ),
        txn AS (
          SELECT t.CUSTOMER_ID, t.BASKET_TOTAL, COALESCE(c.TIER, 'Walk-in') AS segment
          FROM {BRONZE}.BRONZE_TRANSACTIONS t
          LEFT JOIN {BRONZE}.BRONZE_LOYALTY_CUSTOMERS c ON c.CUSTOMER_ID = t.CUSTOMER_ID
        )
        SELECT segment AS SEGMENT,
          COUNT(*) AS TRANSACTIONS,
          AVG(BASKET_TOTAL) AS AVG_BASKET,
          COUNT(DISTINCT CUSTOMER_ID) AS DISTINCT_CUSTOMERS,
          CASE WHEN segment != 'Walk-in'
               THEN (COUNT(*)::FLOAT / COUNT(DISTINCT CUSTOMER_ID)) / (MAX(b.days) / 30.0)
          END AS VISITS_PER_30D
        FROM txn, bounds b
        GROUP BY segment
    """)

    return {
        "summary": {
            "n_stores": summary["N_STORES"],
            "n_customers": summary["N_CUSTOMERS"],
            "n_transactions": summary["N_TRANSACTIONS"],
            "n_events": summary["N_EVENTS"],
            "txn_date_min": str(summary["TXN_DATE_MIN"]),
            "txn_date_max": str(summary["TXN_DATE_MAX"]),
            "event_date_min": str(summary["EVENT_DATE_MIN"]),
            "event_date_max": str(summary["EVENT_DATE_MAX"]),
            "total_signups": summary["TOTAL_SIGNUPS"],
            "total_earns": summary["TOTAL_EARNS"],
            "total_redeems": summary["TOTAL_REDEEMS"],
            "overall_rr": float(summary["OVERALL_RR"]),
            "loyalty_txn_share": float(summary["LOYALTY_TXN_SHARE"]),
            "n_distinct_rewards": summary["N_DISTINCT_REWARDS"],
            "avg_basket_all": float(summary["AVG_BASKET_ALL"]),
        },
        "stores": [
            {
                "store_id": r["STORE_ID"], "name": r["NAME"], "region": r["REGION"], "format": r["FORMAT"],
                "signups": r["SIGNUPS"], "earns": r["EARNS"], "redeems": r["REDEEMS"],
                "rr": float(r["RR"]), "rr_recent": float(r["RR_RECENT"]), "rr_prior": float(r["RR_PRIOR"]),
                "rr_delta": float(r["RR_DELTA"]),
            }
            for r in store_rows
        ],
        "regions": [
            {"region": r["REGION"], "signups": r["SIGNUPS"], "rr": float(r["RR"])}
            for r in region_rows
        ],
        "segments": [
            {
                "segment": r["SEGMENT"], "transactions": r["TRANSACTIONS"],
                "avg_basket": float(r["AVG_BASKET"]), "distinct_customers": r["DISTINCT_CUSTOMERS"],
                "visits_per_30d": float(r["VISITS_PER_30D"]) if r["VISITS_PER_30D"] is not None else None,
            }
            for r in segment_rows
        ],
    }


data = load_dashboard_data()
data["logo_b64"] = branding.LOGO_B64

template_path = Path(__file__).parent / "dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))

components.html(html, height=2700, scrolling=True)
