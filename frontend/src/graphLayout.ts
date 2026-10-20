// Geometry only: this module never edits a workflow's computational declarations.
export type Point={x:number;y:number};
export type Box=Point&{id:string;width:number;height:number};
export type LayoutNode={id:string;width:number;height:number};
export type LayoutEdge={source:string;target:string};
export function layeredLayout(nodes:LayoutNode[],edges:LayoutEdge[],columnGap=170,rowGap=76){
 const order=new Map(nodes.map((n,i)=>[n.id,i])),byId=new Map(nodes.map(n=>[n.id,n]));
 const incoming=new Map(nodes.map(n=>[n.id,new Set<string>()])),outgoing=new Map(nodes.map(n=>[n.id,new Set<string>()]));
 for(const e of edges)if(byId.has(e.source)&&byId.has(e.target)&&e.source!==e.target){incoming.get(e.target)!.add(e.source);outgoing.get(e.source)!.add(e.target);}
 const pending=new Map(nodes.map(n=>[n.id,incoming.get(n.id)!.size]));
 const ranks=new Map(nodes.map(n=>[n.id,n.id==='$input'?0:1]));
 const queue=nodes.filter(n=>pending.get(n.id)===0).map(n=>n.id);let count=0;
 for(let i=0;i<queue.length;i++){const id=queue[i];count++;for(const next of outgoing.get(id)!){ranks.set(next,Math.max(ranks.get(next)!,ranks.get(id)!+1));pending.set(next,pending.get(next)!-1);if(pending.get(next)===0)queue.push(next);}}
 // Invalid drafts may contain cycles; arrange them without hanging or hiding nodes.
 if(count<nodes.length){let rank=Math.max(0,...ranks.values());for(const n of nodes)if(pending.get(n.id)!>0)ranks.set(n.id,++rank);}
 const levels=new Map<number,string[]>();for(const n of nodes){const r=ranks.get(n.id)!;levels.set(r,[...(levels.get(r)||[]),n.id]);}
 const sorted=[...levels.keys()].sort((a,b)=>a-b),slots=new Map<string,number>();
 function update(){for(const ids of levels.values())ids.forEach((id,i)=>slots.set(id,(i+.5)/ids.length));}update();
 for(let pass=0;pass<8;pass++){
  const forward=pass%2===0;
  for(const rank of forward?sorted:[...sorted].reverse()){
   const ids=levels.get(rank)!;
   const score=(id:string)=>{const adj=[...(forward?incoming:outgoing).get(id)!];return adj.length?adj.reduce((v,k)=>v+(slots.get(k)||0),0)/adj.length:slots.get(id)!;};
   const scores=new Map(ids.map(id=>[id,score(id)]));ids.sort((a,b)=>scores.get(a)!-scores.get(b)!||order.get(a)!-order.get(b)!);update();
  }
 }
 const height=(ids:string[])=>ids.reduce((h,id)=>h+byId.get(id)!.height,0)+Math.max(0,ids.length-1)*rowGap;
 const totalHeight=Math.max(0,...[...levels.values()].map(height));
 const positions:Record<string,Point>={};let x=40;
 for(const rank of sorted){const ids=levels.get(rank)!,width=Math.max(...ids.map(id=>byId.get(id)!.width));let y=40+(totalHeight-height(ids))/2;
  for(const id of ids){positions[id]={x,y};y+=byId.get(id)!.height+rowGap;}x+=width+columnGap;
 }
 return {positions,cyclic:count<nodes.length,layers:sorted.map(r=>levels.get(r)!)};
}

export function segmentBlocked(a:Point,b:Point,boxes:Box[]){
 return boxes.some(r=>a.x===b.x?a.x>r.x+.01&&a.x<r.x+r.width-.01&&Math.max(a.y,b.y)>r.y+.01&&Math.min(a.y,b.y)<r.y+r.height-.01:
  a.y===b.y?a.y>r.y+.01&&a.y<r.y+r.height-.01&&Math.max(a.x,b.x)>r.x+.01&&Math.min(a.x,b.x)<r.x+r.width-.01:true);
}
export function orthogonalRoute(source:Point,target:Point,boxes:Box[],lane=0):Point[]{
 // Give different connections parallel tracks instead of stacking every bend
 // on the same obstacle boundary. Keep terminal stubs outside the clearance.
 const pad=18+(lane%8)*4,offset=pad+18;
 const start={x:source.x+offset,y:source.y},end={x:target.x-offset,y:target.y};
 const obstacles=boxes.map(b=>({...b,x:b.x-pad,y:b.y-pad,width:b.width+2*pad,height:b.height+2*pad}));
 const top=Math.min(source.y,target.y,...obstacles.map(b=>b.y))-36-(lane%11)*7;
 const middle=(start.x+end.x)/2+(lane%5-2)*10;
 const direct=source.y===target.y?[source,target]:[source,{x:middle,y:source.y},{x:middle,y:target.y},target];
 const contains=(b:Box,p:Point)=>p.x>=b.x&&p.x<=b.x+b.width&&p.y>=b.y&&p.y<=b.y+b.height;
 const between=obstacles.filter(b=>!contains(b,source)&&!contains(b,target));
 if(end.x>=start.x&&direct.slice(1).every((p,i)=>!segmentBlocked(direct[i],p,between)))return direct;
 const fallback=[source,start,{x:start.x,y:top},{x:end.x,y:top},end,target];
 if(obstacles.length>70)return fallback;
 const xs=[...new Set([start.x,end.x,...obstacles.flatMap(b=>[b.x,b.x+b.width])])].sort((a,b)=>a-b);
 const ys=[...new Set([start.y,end.y,top,...obstacles.flatMap(b=>[b.y,b.y+b.height])])].sort((a,b)=>a-b);
 const width=xs.length,point=(i:number)=>({x:xs[i%width],y:ys[Math.floor(i/width)]});
 const first=ys.indexOf(start.y)*width+xs.indexOf(start.x),last=ys.indexOf(end.y)*width+xs.indexOf(end.x);
 // Direction is part of the state so a bend carries a cost.
 const costs=new Map<string,number>(),prev=new Map<string,string>(),open:{key:string;index:number;dir:number;score:number}[]=[];
 const initial=first+':0';costs.set(initial,0);open.push({key:initial,index:first,dir:0,score:0});let finish='';
 const heuristic=(p:Point)=>Math.abs(p.x-end.x)+Math.abs(p.y-end.y);
 while(open.length){let best=0;for(let i=1;i<open.length;i++)if(open[i].score<open[best].score)best=i;
  const current=open.splice(best,1)[0],a=point(current.index),base=costs.get(current.key)!;
  if(current.index===last){finish=current.key;break;}
  const xi=current.index%width,yi=Math.floor(current.index/width);
  for(const [dx,dy,dir] of [[1,0,1],[-1,0,1],[0,1,2],[0,-1,2]]){
   const nx=xi+dx,ny=yi+dy;if(nx<0||nx>=width||ny<0||ny>=ys.length)continue;
   const index=ny*width+nx,b=point(index);if(segmentBlocked(a,b,obstacles))continue;
   const cost=base+Math.abs(a.x-b.x)+Math.abs(a.y-b.y)+(current.dir&&dir!==current.dir?36:0),key=index+':'+dir;
   if(cost>=(costs.get(key)??Infinity))continue;costs.set(key,cost);prev.set(key,current.key);open.push({key,index,dir,score:cost+heuristic(b)});
  }
 }
 if(!finish)return fallback;
 const route:Point[]=[];for(let k=finish;k;k=prev.get(k)||'')route.push(point(Number(k.split(':')[0])));route.reverse();
 const all=[source,...route,target];return all.filter((p,i)=>i===0||i===all.length-1||!((all[i-1].x===p.x&&p.x===all[i+1].x)||(all[i-1].y===p.y&&p.y===all[i+1].y)));
}
export function routePath(points:Point[],radius=10){
 if(!points.length)return '';
 let path=`M${points[0].x},${points[0].y}`;
 for(let i=1;i<points.length-1;i++){
  const a=points[i-1],b=points[i],c=points[i+1],before=Math.hypot(b.x-a.x,b.y-a.y),after=Math.hypot(c.x-b.x,c.y-b.y);
  const r=Math.min(radius,before/2,after/2);
  if(!r)continue;
  const enter={x:b.x+(a.x-b.x)*r/before,y:b.y+(a.y-b.y)*r/before},leave={x:b.x+(c.x-b.x)*r/after,y:b.y+(c.y-b.y)*r/after};
  path+=` L${enter.x},${enter.y} Q${b.x},${b.y} ${leave.x},${leave.y}`;
 }
 return path+(points.length>1?` L${points.at(-1)!.x},${points.at(-1)!.y}`:'');
}
