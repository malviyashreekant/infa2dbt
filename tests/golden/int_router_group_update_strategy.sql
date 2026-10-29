{{ config(materialized='view') }}

-- Replaces Informatica: RTR_SPLIT.HIGH (Router Group), UPD_HIGH (Update Strategy)
WITH
src_fil_valid AS (
  SELECT * FROM {{ ref('int_fil_valid') }}
),
rtr_split_high AS (
  SELECT src.ORDER_ID AS ORDER_ID1,
         src.AMOUNT AS AMOUNT1,
         src.O_BAND AS O_BAND1,
         src.O_STATUS_DESC AS O_STATUS_DESC1,
         src.O_REGION AS O_REGION1,
         src.O_ORDER_SK AS O_ORDER_SK1
    FROM src_fil_valid src
   WHERE src.O_BAND = 'HIGH'
),
upd_high AS (
  SELECT src.ORDER_ID1 AS ORDER_ID,
         src.AMOUNT1 AS AMOUNT,
         src.O_BAND1 AS O_BAND,
         src.O_STATUS_DESC1 AS O_STATUS_DESC,
         src.O_REGION1 AS O_REGION,
         src.O_ORDER_SK1 AS O_ORDER_SK,
         CASE WHEN src.O_REGION1 IS NULL THEN 3 ELSE CASE WHEN src.O_STATUS_DESC1 = 'Closed' THEN 2 ELSE 1 END END AS INFA_DML_FLAG
    FROM rtr_split_high src
)

SELECT * FROM upd_high
