import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Nero Loyalty Dashboard", layout="wide")
st.title("Caffe Nero — Loyalty & Sales Overview")
st.caption("Reads dbt's gold facts and marts (NERO_ANALYTICS) — never the raw DCM ingestion tables.")

session = get_active_session()


def load(query: str):
    return session.sql(query).to_pandas()


txn_count = load('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".FACT_SALES_TRANSACTION').iloc[0]["N"]
event_count = load('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".FACT_LOYALTY_EVENT').iloc[0]["N"]
customer_count = load('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."03_MART_MARKETING".MART_CUSTOMER_LOYALTY_ACTIVITY').iloc[0]["N"]

col1, col2, col3 = st.columns(3)
col1.metric("Transactions", f"{txn_count:,}")
col2.metric("Loyalty events", f"{event_count:,}")
col3.metric("Customers", f"{customer_count:,}")

release = load('SELECT CURRENT_BATCH_ID, CURRENT_RELEASE_AT FROM NERO_DB."04_METADATA".REPORTING_CURRENT_RELEASE')
if not release.empty and release.iloc[0]["CURRENT_BATCH_ID"] is not None:
    st.caption(f"Current release: `{release.iloc[0]['CURRENT_BATCH_ID']}` "
               f"(published {release.iloc[0]['CURRENT_RELEASE_AT']})")

if txn_count == 0:
    st.info("No transaction data loaded yet — run ingestion/smoke_test.py or a real batch, "
            "then `dbt build`, to populate this view.")
else:
    st.subheader("Basket size by store")
    st.bar_chart(
        load("""
            SELECT s.STORE_NAME, AVG(f.NET_SALES_AMOUNT) AS AVG_BASKET
            FROM NERO_ANALYTICS."02_GOLD".FACT_SALES_TRANSACTION f
            JOIN NERO_ANALYTICS."02_GOLD".DIM_STORE s ON f.STORE_KEY = s.STORE_KEY
            GROUP BY s.STORE_NAME
            ORDER BY AVG_BASKET DESC
        """),
        x="STORE_NAME", y="AVG_BASKET",
    )

    st.subheader("Loyalty events by type")
    st.bar_chart(
        load("""
            SELECT EVENT_TYPE, COUNT(*) AS N
            FROM NERO_ANALYTICS."02_GOLD".FACT_LOYALTY_EVENT
            GROUP BY EVENT_TYPE
        """),
        x="EVENT_TYPE", y="N",
    )

    st.subheader("Store daily performance")
    st.dataframe(
        load('SELECT * FROM NERO_ANALYTICS."04_MART_OPERATIONS".MART_STORE_DAILY_PERFORMANCE ORDER BY PERFORMANCE_DATE DESC')
    )

    st.subheader("Customer loyalty activity")
    st.dataframe(
        load('SELECT * FROM NERO_ANALYTICS."03_MART_MARKETING".MART_CUSTOMER_LOYALTY_ACTIVITY')
    )
