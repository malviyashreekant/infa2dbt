{{ config(materialized='view') }}

-- Replaces Informatica Source Qualifier SQ_CUSTOMERS
SELECT CUSTOMERS.CUST_ID AS CUST_ID,
    CUSTOMERS.CUST_NAME AS CUST_NAME
FROM {{ ref('stg_customers') }} CUSTOMERS
