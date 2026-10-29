{{ config(
    materialized='table',
    alias='fct_orders_low'
) }}

-- Replaces Informatica target FCT_ORDERS_LOW (instance FCT_ORDERS_LOW)
SELECT
    CAST(src.ORDER_ID2 AS NUMBER(10,0)) AS ORDER_ID,
    CAST(src.AMOUNT2 AS NUMBER(12,2)) AS AMOUNT,
    CAST(src.O_BAND2 AS VARCHAR2(4)) AS BAND,
    CAST(src.O_STATUS_DESC2 AS VARCHAR2(10)) AS STATUS_DESC,
    CURRENT_TIMESTAMP AS DBT_UPDATED_AT
FROM {{ ref('int_rtr_split_low') }} src
