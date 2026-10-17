// Read-only: actual subprocess client + localhost API + installed/cached plugin paths.
const assert=require('node:assert/strict');
const path=require('node:path');
const os=require('node:os');
const {spawnSync}=require('node:child_process');
process.env.FLUX_ROOT=path.resolve(__dirname,'..');
async function verify(file,label){
  const tools=new Map();
  require(file).apply({tools:{register(def){tools.set(def.name,def);}},subprocess:{spawn(spec){
    const result=spawnSync(spec.argv[0],spec.argv.slice(1),{cwd:spec.cwd,encoding:'utf8',maxBuffer:8000000,timeout:30000});
    if(result.error||result.status!==0)throw result.error||new Error(result.stderr);
    return {done:Promise.resolve(),collected:{stdout:{readFrom(){return {text:result.stdout};}}}};
  }}});
  for(const name of ['flux_list_modules','flux_list_catalogs','flux_list_definitions','flux_list_naming_rules']){
    const output=await tools.get(name).execute({});
    assert.ok(Array.isArray(output.items),JSON.stringify(output));
    assert.equal(output.count,output.items.length);
    console.log(`${label} ${name}: items array, count=${output.count}`);
  }
}
(async()=>{
  await verify(path.join(os.homedir(),'.dsh/.agent-presets/flux/flux-tools-plugin.js'),'installed');
  await verify(path.resolve(__dirname,'../data/flux-tools-plugin-before-list-fix.js'),'original plugin');
})().catch(e=>{console.error(e);process.exitCode=1;});
