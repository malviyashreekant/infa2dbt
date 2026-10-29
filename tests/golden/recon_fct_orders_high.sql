-- Source-to-target reconciliation for FCT_ORDERS_HIGH (mapping m_orders, model fct_orders_high).
-- Run with: dbt compile --select fct_orders_high ; then execute target/compiled/.../analyses/recon_fct_orders_high.sql
--
-- Part 1: lineage counts. Row counts are expected to differ from the sources because of: FIL_VALID (Filter); RTR_SPLIT.HIGH (Router Group); UPD_HIGH (Update Strategy).
SELECT 'source_rows:CUSTOMERS' AS metric, COUNT(*) AS value FROM {{ source('raw_sales', 'CUSTOMERS') }}
UNION ALL
SELECT 'source_rows:ORDERS' AS metric, COUNT(*) AS value FROM {{ source('raw_sales', 'ORDERS') }}
UNION ALL
SELECT 'target_rows:FCT_ORDERS_HIGH', COUNT(*) FROM {{ ref('fct_orders_high') }};

-- Part 2: parity with the table Informatica loaded (var 'legacy_schema' = its schema).
-- Any variance <> 0 is a FAIL. Hash metrics use ORA_HASH per column (sum of row hashes).
SELECT metric, dbt_value, legacy_value,
       NVL(dbt_value, 0) - NVL(legacy_value, 0) AS variance,
       CASE WHEN NVL(dbt_value, 0) = NVL(legacy_value, 0) THEN 'PASS' ELSE 'FAIL' END AS status
FROM (
SELECT 'row_count' AS metric, (SELECT COUNT(*) FROM {{ ref('fct_orders_high') }}) AS dbt_value, (SELECT COUNT(*) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) AS legacy_value FROM dual
UNION ALL
SELECT 'sum:ORDER_ID', (SELECT SUM(ORDER_ID) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORDER_ID) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'sum:AMOUNT', (SELECT SUM(AMOUNT) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(AMOUNT) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'sum:SK', (SELECT SUM(SK) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(SK) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:ORDER_ID', (SELECT COUNT(ORDER_ID) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(ORDER_ID) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:ORDER_ID', (SELECT SUM(ORA_HASH(ORDER_ID)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(ORDER_ID)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:AMOUNT', (SELECT COUNT(AMOUNT) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(AMOUNT) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:AMOUNT', (SELECT SUM(ORA_HASH(AMOUNT)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(AMOUNT)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:BAND', (SELECT COUNT(BAND) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(BAND) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:BAND', (SELECT SUM(ORA_HASH(BAND)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(BAND)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:STATUS_DESC', (SELECT COUNT(STATUS_DESC) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(STATUS_DESC) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:STATUS_DESC', (SELECT SUM(ORA_HASH(STATUS_DESC)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(STATUS_DESC)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:REGION', (SELECT COUNT(REGION) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(REGION) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:REGION', (SELECT SUM(ORA_HASH(REGION)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(REGION)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'non_null:SK', (SELECT COUNT(SK) FROM {{ ref('fct_orders_high') }}), (SELECT COUNT(SK) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'hash:SK', (SELECT SUM(ORA_HASH(SK)) FROM {{ ref('fct_orders_high') }}), (SELECT SUM(ORA_HASH(SK)) FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH) FROM dual
UNION ALL
SELECT 'rows_only_in_dbt', (SELECT COUNT(*) FROM (SELECT ORDER_ID, AMOUNT, BAND, STATUS_DESC, REGION, SK FROM {{ ref('fct_orders_high') }} MINUS SELECT ORDER_ID, AMOUNT, BAND, STATUS_DESC, REGION, SK FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH)), 0 FROM dual
UNION ALL
SELECT 'rows_only_in_legacy', (SELECT COUNT(*) FROM (SELECT ORDER_ID, AMOUNT, BAND, STATUS_DESC, REGION, SK FROM {{ var('legacy_schema') }}.FCT_ORDERS_HIGH MINUS SELECT ORDER_ID, AMOUNT, BAND, STATUS_DESC, REGION, SK FROM {{ ref('fct_orders_high') }})), 0 FROM dual
)
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, metric;
