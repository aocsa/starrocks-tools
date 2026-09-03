import sys,re
# Condense DuckDB box-drawing EXPLAIN: print each line's non-empty cell texts joined by ' | ', skipping pure borders.
for path in sys.argv[1:]:
    print("=====", path)
    for line in open(path, errors='replace'):
        line=line.rstrip('\n')
        if not line.strip(): continue
        cells=[c.strip() for c in re.split(r'[│┤├]', line)]
        cells=[c for c in cells if c and not re.fullmatch(r'[─┬┴┼┐┌└┘\s]+', c)]
        if not cells: continue
        print(' | '.join(cells))
