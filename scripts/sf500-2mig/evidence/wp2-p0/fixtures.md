budget=2147483648 nodes=2 ratio=4.0
| query | joins | PASS | FAIL | UNKNOWN | failing / unknown edges |
|---|---|---|---|---|---|
| both_large_shuffle | 1 | 1 | 0 | 0 |  |
| large_broadcast | 1 | 0 | 1 | 0 | 3:BROADCAST FAIL R1 broadcast build 4.8e+09 B x 2 > budget 2147483648 |
| shuffle_beside_small | 1 | 0 | 1 | 0 | 5:PARTITIONED FAIL R2 shuffle of 9.6e+10 B beside a budget-fitting 4e+05 B partner |
| small_broadcast | 1 | 1 | 0 | 0 |  |
| unknown_card_one | 1 | 0 | 0 | 1 | 3:PARTITIONED UNKNOWN build=unknown probe=unknown |
