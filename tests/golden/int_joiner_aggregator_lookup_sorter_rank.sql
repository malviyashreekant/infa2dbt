{{ config(materialized='view') }}

-- Replaces Informatica: JNR_CUST_ORDERS (Joiner), AGG_CUST (Aggregator), LKP_SEG (Lookup Procedure), SRT_CUST (Sorter), RNK_TOP (Rank)
WITH
src_sq_customers AS (
  SELECT * FROM {{ ref('stg_sq_customers') }}
),
src_sq_orders AS (
  SELECT * FROM {{ ref('stg_sq_orders') }}
),
jnr_cust_orders AS (
  SELECT m.CUST_ID AS CUST_ID,
         m.CUST_NAME AS CUST_NAME,
         d.AMOUNT AS AMOUNT
    FROM src_sq_customers m
   LEFT OUTER JOIN src_sq_orders d ON m.CUST_ID = d.CUST_ID
),
agg_cust AS (
  SELECT src.CUST_ID AS CUST_ID,
         src.CUST_NAME AS CUST_NAME,
         SUM(src.AMOUNT) AS O_TOTAL,
         COUNT(src.AMOUNT) AS O_CNT
    FROM jnr_cust_orders src
   GROUP BY src.CUST_ID, src.CUST_NAME
),
lkp_seg__lkp AS (
  SELECT l.*, ROW_NUMBER() OVER (PARTITION BY CUST_ID ORDER BY CUST_ID ASC, SEGMENT ASC) AS RN__
    FROM (
      SELECT CUST_ID, SEGMENT
      FROM {{ source('raw_sales', 'CUSTOMERS') }}
    ) l
),
lkp_seg AS (
  SELECT src.CUST_ID AS IN_CUST_ID,
         src.CUST_NAME AS IN_CUST_NAME,
         src.O_TOTAL AS IN_TOTAL,
         src.O_CNT AS IN_CNT,
         lkp.SEGMENT AS SEGMENT
    FROM agg_cust src
   LEFT OUTER JOIN lkp_seg__lkp lkp ON lkp.CUST_ID = src.CUST_ID AND lkp.RN__ = 1
),
srt_cust AS (
  SELECT src.IN_CUST_ID AS IN_CUST_ID,
         src.IN_CUST_NAME AS IN_CUST_NAME,
         src.IN_TOTAL AS IN_TOTAL,
         src.IN_CNT AS IN_CNT,
         src.SEGMENT AS SEGMENT
    FROM lkp_seg src
   ORDER BY IN_CUST_ID ASC NULLS LAST
),
rnk_top AS (
  SELECT src.IN_CUST_ID AS IN_CUST_ID,
         src.IN_CUST_NAME AS IN_CUST_NAME,
         src.IN_TOTAL AS IN_TOTAL,
         src.IN_CNT AS IN_CNT,
         src.SEGMENT AS SEGMENT,
         CAST(NULL AS NUMBER(10,0)) AS RANKINDEX
    FROM srt_cust src
  /* NEEDS_REVIEW: Rank is not auto-converted: use RANK()/ROW_NUMBER() OVER (PARTITION BY group ports ORDER BY rank port) and filter on the Top/Bottom N attribute */
)

SELECT * FROM rnk_top
