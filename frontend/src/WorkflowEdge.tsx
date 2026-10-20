import {BaseEdge,EdgeLabelRenderer,useStore,type EdgeProps} from '@xyflow/react';
import {memo,useMemo} from 'react';
import {orthogonalRoute,routePath,type Box} from './graphLayout';

// Subscribe to geometry alone: pointer movement must not rebuild obstacle routes.
function WorkflowEdge(props:EdgeProps){
 const signature=useStore(s=>JSON.stringify([...s.nodeLookup.values()].map(n=>({id:n.id,x:n.position.x,y:n.position.y,width:n.measured?.width||245,height:n.measured?.height||180}))));
 const points=useMemo(()=>orthogonalRoute({x:props.sourceX,y:props.sourceY},{x:props.targetX,y:props.targetY},JSON.parse(signature) as Box[],Number(props.data?.lane||0)),[signature,props.sourceX,props.sourceY,props.targetX,props.targetY,props.data?.lane]);
 const path=useMemo(()=>routePath(points),[points]);
 const segment=points.slice(1).map((p,i)=>({a:points[i],b:p,length:Math.hypot(p.x-points[i].x,p.y-points[i].y)})).sort((a,b)=>b.length-a.length)[0];
 const label=segment?{x:(segment.a.x+segment.b.x)/2,y:(segment.a.y+segment.b.y)/2}:points[0];
 return <><title>{String(props.label||'')}</title><BaseEdge id={props.id} path={path} markerEnd={props.markerEnd} style={{...props.style,vectorEffect:'non-scaling-stroke'}} interactionWidth={14}/>{props.label&&props.selected&&<EdgeLabelRenderer><span className="workflow-edge-label nodrag nopan" style={{transform:`translate(-50%, -50%) translate(${label.x}px,${label.y}px)`}}>{props.label}</span></EdgeLabelRenderer>}</>;
}
export default memo(WorkflowEdge);
