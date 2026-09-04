23. q09 at 4 CNs with STAGING=72GiB and GPU_MEM=64GiB: see arms/dp-cn4-q09-stg72 (runs/runs.csv, cnlog.txt, quent.txt, compare).
   outcome: passed the arena but died on the receiver's GPU pool: 'failed to push a staged remote batch from sender 0 into stream 4: std::bad_alloc: out_of_memory' after 10.5 s (pool 64 GiB).
24. q09 at 4 CNs with STAGING=72GiB and GPU_MEM=100GiB: arms/dp-cn4-q09-stg72-pool100; outcome: q09 r0 cold fail 8340ms rows=0 ERROR 1064 (HY000) at line 1: failed to execute fragment: std::bad_alloc: out_of_memory: not enough capacity to allocate memory backend [id=10001] [  
