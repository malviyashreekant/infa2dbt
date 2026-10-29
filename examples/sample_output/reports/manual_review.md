# Manual review report

Everything below was converted with a stand-in or not at all. Each item must be resolved by a person.

## Mapping `m_customer_summary`

1. **m_customer_summary:RNK_TOP**: Rank is not auto-converted: use RANK()/ROW_NUMBER() OVER (PARTITION BY group ports ORDER BY rank port) and filter on the Top/Bottom N attribute
2. **m_customer_summary:SP_AUDIT**: Stored Procedure has no connections, so it cannot be converted; if it is called from an expression or session, replace it manually


# Warnings (behaviour that may differ from PowerCenter)

- m_customer_summary:JNR_CUST_ORDERS: join keys: PowerCenter may treat NULL keys as equal, SQL does not; verify NULL join keys
- m_customer_summary:LKP_SEG: first/last match uses PowerCenter's default ORDER BY on the lookup ports; verify
- m_customer_summary:SRT_CUST: ORDER BY is kept for traceability; it only matters if a later step depends on row order
- m_customer_summary:DIM_CUST_SUMMARY: no Update Strategy: assumed truncate-and-load (table materialization)
- m_orders:SQ_ORDERS_CUSTOMERS: Source Qualifier join/filter copied verbatim (SQL); check column qualifiers
- m_orders:LKP_REGION: first/last match uses PowerCenter's default ORDER BY on the lookup ports; verify
- m_orders:SEQ_KEY: ROW_NUMBER() surrogate keys restart every run and are not stable across runs
- m_orders:FCT_ORDERS_LOW: no Update Strategy: assumed truncate-and-load (table materialization)
