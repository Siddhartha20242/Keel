// Why is A connected to B? Shortest call route (FalkorDB algo.SPpaths).
MATCH (a:Symbol {qname: $from}), (b:Symbol {qname: $to})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['CALLS'], relDirection: 'outgoing', weightProp: 'w', pathCount: 1})
YIELD path, pathWeight
RETURN [n IN nodes(path) | n.qname] AS hops, pathWeight
