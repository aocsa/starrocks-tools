import re,glob,os,sys
# Summarize each FE fragment dump: node types (decoded), scanned tables, sink kind/partition, #dests
tmap={}
for l in open('survey/TPlanNodeType.map.txt'):
    m=re.match(r'\s*(\d+)\s*[:=]?\s*(\w+)',l)
    if m: tmap[int(m.group(1))]=m.group(2)
files=sorted(glob.glob(sys.argv[1]+'/fragment-*.txt'))
for f in files:
    t=open(f,errors='replace').read()
    nodes=re.findall(r'node_id: (\d+),\s*node_type: TPlanNodeType\(\s*(\d+),?\s*\)',t)
    types=[f"{nid}:{tmap.get(int(ty),ty)}" for nid,ty in nodes]
    tables=sorted(set(re.findall(r'tpch_sf1000/(\w+)',t)))
    sink=re.search(r'output_sink: Some\(\s*TDataSink \{\s*type_: TDataSinkType\(\s*(\d+)',t)
    ptype=re.search(r'output_partition: Some\(\s*TDataPartition \{\s*type_: TPartitionType\(\s*(\d+)',t)
    ndest=len(re.findall(r'TPlanFragmentDestination \{',t))
    exch=re.findall(r'exchange_node: Some',t)
    qid=re.search(r'query_id: Some\(\s*TUniqueId \{\s*hi: (-?\d+),\s*lo: (-?\d+)',t)
    print(os.path.basename(f), 'q=%s'%(qid.group(1)[-6:] if qid else '?'), 'nodes=['+','.join(types)+']', 'tables='+','.join(tables), 'sink=%s part=%s dests=%d'%(sink.group(1) if sink else '-', ptype.group(1) if ptype else '-', ndest))
