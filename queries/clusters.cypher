// Weakly connected components of the strong co-change graph (FalkorDB algo.WCC; CO_STRONG = pairs at least 35% as strong as the
// strongest pair): files that keep changing together end up in one component. Used to ask "this PR touched A; B always changes with A; did you forget B?"
CALL algo.WCC({nodeLabels: ['File'], relationshipTypes: ['CO_STRONG']}) YIELD node, componentId
WHERE node.kind = 'src'
RETURN componentId AS component, collect(node.path) AS files, count(node) AS size
ORDER BY size DESC
