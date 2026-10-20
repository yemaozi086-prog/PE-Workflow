const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path'),ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync(path.join(__dirname,'../src/graphLayout.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const out={};vm.runInNewContext(code,{exports:out});
const {layeredLayout,orthogonalRoute,segmentBlocked}=out;
const ids=['$input','point','inductor','rms','hs','ls','cu','esr','switching','ripple','total','temperature','evaluation','$output'];
const nodes=ids.map((id,i)=>({id,width:225+i%3*15,height:id==='$output'?610:140+i%4*48}));
const pairs=[['$input','point'],['point','inductor'],['$input','inductor'],['inductor','rms'],['point','rms'],['rms','hs'],['rms','ls'],['rms','cu'],['rms','esr'],['inductor','switching'],['inductor','ripple'],['hs','total'],['ls','total'],['cu','total'],['esr','total'],['switching','total'],['hs','temperature'],['ls','temperature'],['switching','temperature'],['total','evaluation'],['temperature','evaluation'],['ripple','evaluation']];
for(const id of ids)if(!id.startsWith('$'))pairs.push([id,'$output']);
const edges=pairs.map(([source,target])=>({source,target})),original=JSON.stringify({nodes,edges});
const result=layeredLayout(nodes,edges);assert.equal(result.cyclic,false);assert.equal(Object.keys(result.positions).length,nodes.length);
for(const e of edges)assert(result.positions[e.target].x>result.positions[e.source].x,'Every dependency flows right');
assert.equal(result.positions.hs.x,result.positions.ls.x);assert.notEqual(result.positions.hs.y,result.positions.ls.y);
assert(result.layers.length<nodes.length,'Buck branches must not become one long row');
for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
 const a={...nodes[i],...result.positions[nodes[i].id]},b={...nodes[j],...result.positions[nodes[j].id]};
 assert(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y,'Measured cards must not overlap');
}
assert.equal(JSON.stringify({nodes,edges}),original,'Layout must not mutate graph');
assert.equal(JSON.stringify(result),JSON.stringify(layeredLayout(nodes,edges)),'Layout must be deterministic');
const cyclic=layeredLayout(nodes,[...edges,{source:'evaluation',target:'point'}]);assert(cyclic.cyclic);assert(Object.values(cyclic.positions).every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y)));
const obstacle={id:'block',x:180,y:-50,width:120,height:100};
const route=orthogonalRoute({x:0,y:0},{x:600,y:0},[obstacle]);
for(let i=1;i<route.length;i++){
 assert(route[i].x===route[i-1].x||route[i].y===route[i-1].y,'Routes must be orthogonal');
 assert.equal(segmentBlocked(route[i-1],route[i],[obstacle]),false,'Long connections must avoid cards');
}
assert.equal(route[0].x,0);assert.equal(route.at(-1).x,600);
assert.equal(Object.keys(layeredLayout([],[]).positions).length,0);
console.log('Graph layout checks passed: Buck layering, measured non-overlap, deterministic geometry, cycles, routing and no graph mutation.');
const rounded=out.routePath([{x:0,y:0},{x:100,y:0},{x:100,y:80},{x:220,y:80}]);
assert(rounded.includes('Q'),'Routing renders rounded bends');
assert(rounded.startsWith('M0,0'));assert(rounded.endsWith('L220,80'));
const simple=orthogonalRoute({x:0,y:0},{x:600,y:120},[]);
assert.equal(simple.length,4,'Unobstructed connections use just two bends');
assert.equal(JSON.stringify(simple),JSON.stringify(orthogonalRoute({x:0,y:0},{x:600,y:120},[])),'Unchanged geometry has stable routes');
assert.notEqual(JSON.stringify(simple),JSON.stringify(orthogonalRoute({x:0,y:0},{x:600,y:120},[],1)),'Parallel connections have separate bend tracks');
assert.equal(out.routePath([]),'');
console.log('Rounded routing checks passed: stable paths, separate tracks, simple bends and exact endpoints.');
