{{ config(
    materialized='table',
    alias='dim_cust_summary'
) }}

-- Replaces Informatica target DIM_CUST_SUMMARY (instance DIM_CUST_SUMMARY)
SELECT
    CAST(src.IN_CUST_ID AS NUMBER(10,0)) AS CUST_ID,
    CAST(src.IN_CUST_NAME AS VARCHAR2(50)) AS CUST_NAME,
    CAST(src.SEGMENT AS VARCHAR2(10)) AS SEGMENT,
    CAST(src.IN_TOTAL AS NUMBER(14,0)) AS TOTAL_AMT,
    CAST(src.IN_CNT AS NUMBER(10,0)) AS ORDER_CNT,
    CURRENT_TIMESTAMP AS DBT_UPDATED_AT
FROM {{ ref('int_rnk_top') }} src
