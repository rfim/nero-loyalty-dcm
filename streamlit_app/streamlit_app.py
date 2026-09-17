import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Nero Loyalty Dashboard", layout="wide")
st.title("Caffe Nero — Loyalty & Sales Overview")

session = get_active_session()

def load(query: str):
    return session.sql(query).to_pandas()

txn_count = load("SELECT COUNT(*) AS N FROM NERO_DB.NERO_LOYALTY.FACT_TRANSACTIONS").iloc[0]["N"]
event_count = load("SELECT COUNT(*) AS N FROM NERO_DB.NERO_LOYALTY.FACT_LOYALTY_EVENTS").iloc[0]["N"]
customer_count = load("SELECT COUNT(*) AS N FROM NERO_DB.NERO_LOYALTY.DIM_CUSTOMER").iloc[0]["N"]

col1, col2, col3 = st.columns(3)
col1.metric("Transactions", f"{txn_count:,}")
col2.metric("Loyalty events", f"{event_count:,}")
col3.metric("Customers", f"{customer_count:,}")

if txn_count == 0:
    st.info("No transaction data loaded yet — this schema is deployed but empty. "
            "Load stores/customers/transactions/loyalty_events to populate this view.")
else:
    st.subheader("Basket size by store")
    st.bar_chart(
        load("""
            SELECT s.STORE_NAME, AVG(f.BASKET_TOTAL) AS AVG_BASKET
            FROM NERO_DB.NERO_LOYALTY.FACT_TRANSACTIONS f
            JOIN NERO_DB.NERO_LOYALTY.DIM_STORE s ON f.STORE_SK = s.STORE_SK
            GROUP BY s.STORE_NAME
            ORDER BY AVG_BASKET DESC
        """),
        x="STORE_NAME", y="AVG_BASKET",
    )

    st.subheader("Loyalty events by type")
    st.bar_chart(
        load("""
            SELECT EVENT_TYPE, COUNT(*) AS N
            FROM NERO_DB.NERO_LOYALTY.FACT_LOYALTY_EVENTS
            GROUP BY EVENT_TYPE
        """),
        x="EVENT_TYPE", y="N",
    )
