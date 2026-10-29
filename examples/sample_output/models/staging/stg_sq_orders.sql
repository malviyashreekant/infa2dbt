{{ config(materialized='view') }}

-- Replaces Informatica Source Qualifier SQ_ORDERS
SELECT ORDERS.CUST_ID AS CUST_ID,
    ORDERS.AMOUNT AS AMOUNT
FROM {{ ref('stg_orders') }} ORDERS
