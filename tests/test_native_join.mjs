// Read-only native join check; no Python adapter or synthetic schema.
import {execFileSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {buildSnapshot} from '../desktop/model.mjs';
const root='/home/hermes/.hermes/work/beads-lab';
const read=(...args)=>JSON.parse(execFileSync('flock',[
  `${root}/planning-access.lock`,`${root}/bin/bd`,'-C',`${root}/planning`,
  '--readonly','--actor','lab-owner',...args,'--json'],{encoding:'utf8'}));
const info=read('info');
const rows=read('list','--all','--limit','0');
const ready=read('ready','--limit','0');
const blocked=read('blocked');
const snap=buildSnapshot({issues:rows,ready,blocked,
  storeInfo:{workspace:`${root}/planning`,db:info.database_path}},{bound:1000});
assert.equal(snap.byId.size,rows.length);
for(const row of rows) assert.equal(snap.nodes.get(row.id).parent,row.parent??null);
for(const row of ready) if(snap.nodes.has(row.id)) assert.equal(snap.nodes.get(row.id).derivedBlocked,false);
for(const row of blocked) if(snap.nodes.has(row.id)&&!ready.some(r=>r.id===row.id)) assert.equal(snap.nodes.get(row.id).derivedBlocked,true);
console.log(JSON.stringify({pass:true,rows:rows.length,ready:ready.length,blocked:blocked.length}));
