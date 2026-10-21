const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),assert=require('node:assert/strict');
const project=path.resolve(__dirname,'..');
const base=fs.mkdtempSync(path.join(project,'data','quick-transport-test-'));
const preset=path.join(base,'preset');fs.mkdirSync(preset);
fs.copyFileSync(path.join(project,'dsh/preset/pe-quick/quick-tools.js'),path.join(preset,'quick-tools.js'));
fs.writeFileSync(path.join(preset,'flux-root.txt'),base);
const tools={},sent=[];
require(path.join(preset,'quick-tools.js')).apply({
 tools:{register:t=>tools[t.name]=t},
 subprocess:{spawn:options=>{
  sent.push(JSON.parse(fs.readFileSync(options.argv[2],'utf8')));
  return {done:Promise.resolve(),collected:{stdout:{readFrom:()=>({text:'{"status":"checked"}'})}}};
 }}
});
(async()=>{
 const args={chat_id:'a'.repeat(32),turn_id:'b'.repeat(32),token:'isolated-test',bundle:{workflow:{},modules:[],summary:'test'}};
 assert.equal((await tools.quick_check.execute(args)).status,'checked');
 assert.equal((await tools.quick_submit.execute({arguments:JSON.stringify(args)})).status,'checked');
 assert.equal((await tools.quick_check.execute('{"chat_id":')).status,'envelope_error');
 assert.equal((await tools.quick_check.execute({arguments:{bundle:{}}})).status,'envelope_error');
 assert.equal(sent.length,2);assert.equal(sent[1].chat_id,args.chat_id);assert.equal(sent[1].operation,'submit');
 console.log('Quick transport: exact wrapped envelopes accepted; malformed/missing credentials rejected before bridge');
})().catch(e=>{console.error(e);process.exitCode=1;});
