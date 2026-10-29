{% macro lkp_lkp_region(arg1) %}
(SELECT lkp.REGION_NAME
   FROM (SELECT l.*, ROW_NUMBER() OVER (PARTITION BY CUST_ID ORDER BY CUST_ID ASC, REGION_NAME ASC) AS RN__ FROM (
    SELECT CUST_ID, REGION_NAME
    FROM {{ source('raw_sales', 'CUST_REGION') }}
  ) l) lkp
  WHERE lkp.CUST_ID = {{ arg1 }} AND lkp.RN__ = 1)
{% endmacro %}
