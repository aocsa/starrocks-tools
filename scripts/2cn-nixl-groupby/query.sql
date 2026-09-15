SELECT region, SUM(amount)
FROM FILES("path"="file:///opt/dlami/nvme/tmp/sirius-2cn-e2e/sales/sales_*.parquet","format"="parquet")
GROUP BY region
