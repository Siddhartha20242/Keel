// Module blast radius: how many source files import each edited file, directly or through other files (up to 4 imports deep),
// using FalkorDB's BFS over IMPORTED_BY (IMPORTS reversed). Files nothing imports return no row; the toolbox defaults them to 0.
UNWIND $paths AS p
MATCH (f:File {path: p})
CALL algo.BFS(f, 4, 'IMPORTED_BY') YIELD nodes
RETURN f.path AS file, size([n IN nodes WHERE n.kind = 'src']) AS importers
