-- Source-to-target reconciliation for DIM_CUST_SUMMARY (mapping m_customer_summary, model dim_cust_summary).
-- Run with: dbt compile --select dim_cust_summary ; then execute target/compiled/.../analyses/recon_dim_cust_summary.sql
--
-- Part 1: lineage counts. Row counts are expected to differ from the sources because of: AGG_CUST (Aggregator); JNR_CUST_ORDERS (Joiner); RNK_TOP (Rank).
SELECT 'source_rows:CUSTOMERS' AS metric, COUNT(*) AS value FROM {{ source('raw_sales', 'CUSTOMERS') }}
UNION ALL
SELECT 'source_rows:ORDERS' AS metric, COUNT(*) AS value FROM {{ source('raw_sales', 'ORDERS') }}
UNION ALL
SELECT 'target_rows:DIM_CUST_SUMMARY', COUNT(*) FROM {{ ref('dim_cust_summary') }};

-- Part 2: parity with the table Informatica loaded (var 'legacy_schema' = its schema).
-- Any variance <> 0 is a FAIL. Hash metrics use ORA_HASH per column (sum of row hashes).
SELECT metric, dbt_value, legacy_value,
       NVL(dbt_value, 0) - NVL(legacy_value, 0) AS variance,
       CASE WHEN NVL(dbt_value, 0) = NVL(legacy_value, 0) THEN 'PASS' ELSE 'FAIL' END AS status
FROM (
SELECT 'row_count' AS metric, (SELECT COUNT(*) FROM {{ ref('dim_cust_summary') }}) AS dbt_value, (SELECT COUNT(*) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) AS legacy_value FROM dual
UNION ALL
SELECT 'sum:CUST_ID', (SELECT SUM(CUST_ID) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(CUST_ID) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'sum:TOTAL_AMT', (SELECT SUM(TOTAL_AMT) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(TOTAL_AMT) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'sum:ORDER_CNT', (SELECT SUM(ORDER_CNT) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORDER_CNT) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'non_null:CUST_ID', (SELECT COUNT(CUST_ID) FROM {{ ref('dim_cust_summary') }}), (SELECT COUNT(CUST_ID) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'hash:CUST_ID', (SELECT SUM(ORA_HASH(CUST_ID)) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORA_HASH(CUST_ID)) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'non_null:CUST_NAME', (SELECT COUNT(CUST_NAME) FROM {{ ref('dim_cust_summary') }}), (SELECT COUNT(CUST_NAME) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'hash:CUST_NAME', (SELECT SUM(ORA_HASH(CUST_NAME)) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORA_HASH(CUST_NAME)) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'non_null:SEGMENT', (SELECT COUNT(SEGMENT) FROM {{ ref('dim_cust_summary') }}), (SELECT COUNT(SEGMENT) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'hash:SEGMENT', (SELECT SUM(ORA_HASH(SEGMENT)) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORA_HASH(SEGMENT)) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'non_null:TOTAL_AMT', (SELECT COUNT(TOTAL_AMT) FROM {{ ref('dim_cust_summary') }}), (SELECT COUNT(TOTAL_AMT) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'hash:TOTAL_AMT', (SELECT SUM(ORA_HASH(TOTAL_AMT)) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORA_HASH(TOTAL_AMT)) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'non_null:ORDER_CNT', (SELECT COUNT(ORDER_CNT) FROM {{ ref('dim_cust_summary') }}), (SELECT COUNT(ORDER_CNT) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'hash:ORDER_CNT', (SELECT SUM(ORA_HASH(ORDER_CNT)) FROM {{ ref('dim_cust_summary') }}), (SELECT SUM(ORA_HASH(ORDER_CNT)) FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY) FROM dual
UNION ALL
SELECT 'rows_only_in_dbt', (SELECT COUNT(*) FROM (SELECT CUST_ID, CUST_NAME, SEGMENT, TOTAL_AMT, ORDER_CNT FROM {{ ref('dim_cust_summary') }} MINUS SELECT CUST_ID, CUST_NAME, SEGMENT, TOTAL_AMT, ORDER_CNT FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY)), 0 FROM dual
UNION ALL
SELECT 'rows_only_in_legacy', (SELECT COUNT(*) FROM (SELECT CUST_ID, CUST_NAME, SEGMENT, TOTAL_AMT, ORDER_CNT FROM {{ var('legacy_schema') }}.DIM_CUST_SUMMARY MINUS SELECT CUST_ID, CUST_NAME, SEGMENT, TOTAL_AMT, ORDER_CNT FROM {{ ref('dim_cust_summary') }})), 0 FROM dual
)
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, metric;
