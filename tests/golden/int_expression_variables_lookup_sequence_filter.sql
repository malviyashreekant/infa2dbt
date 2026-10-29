{{ config(materialized='view') }}

-- Replaces Informatica: EXP_CALC (Expression), FIL_VALID (Filter)
WITH
src_sq_orders_customers AS (
  SELECT * FROM {{ ref('stg_sq_orders_customers') }}
),
exp_calc__in AS (
  SELECT src.ORDER_ID AS ORDER_ID,
         src.CUST_ID AS CUST_ID,
         src.AMOUNT AS AMOUNT,
         src.STATUS AS STATUS,
         src.ORDER_DT AS ORDER_DT,
         src.CUST_NAME AS CUST_NAME,
         (1000 + (ROW_NUMBER() OVER (ORDER BY NULL) - 1) * 1) AS SK
    FROM src_sq_orders_customers src
),
exp_calc__v1 AS (
  SELECT s.*,
         ROUND(s.AMOUNT, 0) AS V_AMT_ROUNDED
    FROM exp_calc__in s
),
exp_calc__v2 AS (
  SELECT s.*,
         CASE WHEN s.V_AMT_ROUNDED > 1000 THEN 'HIGH' ELSE 'LOW' END AS V_BAND
    FROM exp_calc__v1 s
),
exp_calc AS (
  SELECT s.ORDER_ID AS ORDER_ID,
         s.AMOUNT AS AMOUNT,
         s.ORDER_DT AS ORDER_DT,
         s.CUST_NAME AS CUST_NAME,
         s.V_BAND AS O_BAND,
         CASE s.STATUS WHEN 'O' THEN 'Open' WHEN 'C' THEN 'Closed' ELSE 'Other' END AS O_STATUS_DESC,
         {{ lkp_lkp_region('s.CUST_ID') }} AS O_REGION,
         EXTRACT(YEAR FROM s.ORDER_DT) AS O_ORDER_YEAR,
         s.SK AS O_ORDER_SK
    FROM exp_calc__v2 s
),
fil_valid AS (
  SELECT src.ORDER_ID AS ORDER_ID,
         src.AMOUNT AS AMOUNT,
         src.O_BAND AS O_BAND,
         src.O_STATUS_DESC AS O_STATUS_DESC,
         src.O_REGION AS O_REGION,
         src.O_ORDER_SK AS O_ORDER_SK
    FROM exp_calc src
   WHERE (src.AMOUNT > 0 AND NOT (src.ORDER_ID IS NULL))
)

SELECT * FROM fil_valid
