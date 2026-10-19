const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const z=require('/Users/cc/workspace/zvec-grep/node_modules/@zvec/zvec');
const N=150,DIM=64;
const mode=process.argv[2];
const db=process.argv[3];
function emit(x){fs.writeSync(1,JSON.stringify(x)+'\n');}
function doc(i){return {id:String(i),fields:{ordinal:i},vectors:{embedding:Array.from({length:DIM},(_,j)=>(i+j+1)/1024)}};}
function schema(){return new z.ZVecCollectionSchema({name:'q001_auxiliary_smoke',fields:{name:'ordinal',dataType:z.ZVecDataType.INT32,nullable:false},vectors:{name:'embedding',dataType:z.ZVecDataType.VECTOR_FP32,dimension:DIM,indexParams:{indexType:z.ZVecIndexType.HNSW,metricType:z.ZVecMetricType.COSINE}}});}
function snapshot(c,label){const ids=Array.from({length:N},(_,i)=>String(i));const got=c.fetchSync({ids,includeVector:true});const missing=[],wrongScalar=[],wrongVector=[];let maxAbsVectorError=0;for(let i=0;i<N;i++){const d=got[String(i)];if(!d){missing.push(i);continue;}if(d.fields.ordinal!==i)wrongScalar.push(i);const v=d.vectors.embedding;if(!v || v.length!==DIM)wrongVector.push(i);else {const errors=doc(i).vectors.embedding.map((x,j)=>Math.abs(v[j]-x));maxAbsVectorError=Math.max(maxAbsVectorError,...errors);if(errors.some(x=>!Number.isFinite(x)||x>1e-6))wrongVector.push(i);}}const r={phase:label,pid:process.pid,stats:c.stats,fetched:Object.keys(got).length,missing,wrongScalar,wrongVector,maxAbsVectorError,vectorAbsTolerance:1e-6};emit(r);return r;}
function check(s){assert.equal(s.stats.docCount,N);assert.equal(s.fetched,N);assert.deepEqual(s.missing,[]);assert.deepEqual(s.wrongScalar,[]);assert.deepEqual(s.wrongVector,[]);}
if(mode==='crash-create' || mode==='control-create-optimize'){
 const c=z.ZVecCreateAndOpen(db,schema());
 for(let i=0;i<N;i++){const st=c.insertSync(doc(i));assert.equal(st.ok,true,JSON.stringify(st));}
 const before=snapshot(c,mode==='crash-create'?'inserted-before-sigkill':'control-before-optimize');check(before);
 if(mode==='crash-create'){process.kill(process.pid,'SIGKILL');throw Error('SIGKILL did not terminate');}
 c.optimizeSync();check(snapshot(c,'control-after-optimize-same-handle'));c.closeSync();
}else if(mode==='recover-probe'){
 const c=z.ZVecOpen(db);check(snapshot(c,'recovered-before-second-sigkill'));process.kill(process.pid,'SIGKILL');throw Error('SIGKILL did not terminate');
}else if(mode==='recover-optimize'){
 const c=z.ZVecOpen(db);check(snapshot(c,'recovered-before-optimize'));c.optimizeSync();check(snapshot(c,'after-optimize-same-handle'));c.closeSync();
}else if(mode==='check'){
 const c=z.ZVecOpen(db);check(snapshot(c,'fresh-process-reopen'));c.closeSync();
}else if(mode==='parent'){
 const out=process.argv[3];const steps=[['crash-create','crash-db'],['recover-probe','crash-db'],['recover-optimize','crash-db'],['check','crash-db'],['control-create-optimize','control-db'],['check','control-db']];const results=[];
 for(let i=0;i<steps.length;i++){const [m,p]=steps[i];const t=Date.now();const r=spawnSync(process.execPath,[__filename,m,path.join(out,p)],{cwd:out,encoding:'utf8',timeout:30000,maxBuffer:4*1024*1024,env:{...process.env}});fs.writeFileSync(path.join(out,`${i+1}-${m}.stdout`),r.stdout||'');fs.writeFileSync(path.join(out,`${i+1}-${m}.stderr`),r.stderr||'');const killed=m==='crash-create'||m==='recover-probe';const row={mode:m,collection:p,status:r.status,signal:r.signal,error:r.error?String(r.error):null,elapsedMs:Date.now()-t,expectedForcedCrash:killed,passed:killed?r.signal==='SIGKILL':r.status===0,observations:(r.stdout||'').split('\n').filter(x=>x.startsWith('{')).map(x=>JSON.parse(x))};results.push(row);if(!row.passed)break;}
 const summary={kind:'auxiliary-installed-native-package-smoke',packageVersion:'0.7.0',frozenCommitValidated:false,limitations:['Installed npm native package cannot establish behavior of frozen ec8a78e source or isolate PR600.','No query-ranking check; all known IDs, scalar values, and 64-d FP32 vectors checked with absolute tolerance 1e-6.'],node:process.version,platform:process.platform,arch:process.arch,documentCount:N,dimension:DIM,steps:results,passed:results.length===steps.length&&results.every(x=>x.passed)};fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(summary,null,2)+'\n');emit(summary);if(!summary.passed)process.exitCode=1;
}else throw new Error('bad mode');
