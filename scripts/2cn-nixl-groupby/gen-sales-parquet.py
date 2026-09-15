#!/usr/bin/env python3
"""Write the two tiny sales parquet shards the 2-CN GROUP BY e2e uses."""
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.parquet as pq

out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent / "data")
out.mkdir(parents=True, exist_ok=True)
schema = pa.schema([("region", pa.string()), ("amount", pa.int64())])
pq.write_table(
    pa.table(
        {
            "region": ["east", "west", "east", "north", "south"],
            "amount": [10, 20, 5, 3, 8],
        },
        schema=schema,
    ),
    out / "sales_0.parquet",
)
pq.write_table(
    pa.table(
        {
            "region": ["west", "east", "north", "south", "midwest"],
            "amount": [7, 3, 11, 1, 4],
        },
        schema=schema,
    ),
    out / "sales_1.parquet",
)
print(f"wrote {out / 'sales_0.parquet'} and {out / 'sales_1.parquet'}")
