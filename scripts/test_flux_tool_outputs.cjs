const assert = require('node:assert/strict');
const path = require('node:path');
process.env.FLUX_ROOT = path.resolve(__dirname, '..');
const plugin = require('../dsh/preset/flux/flux-tools-plugin.js');
let response;
const tools = new Map();
plugin.apply({
  tools: { register(def) { tools.set(def.name, def); } },
  subprocess: { spawn() { return { done: Promise.resolve(), collected: { stdout: { readFrom() { return { text: JSON.stringify(response) }; } } } }; } },
});
(async () => {
  for (const name of ['flux_list_modules','flux_list_catalogs','flux_list_definitions','flux_list_naming_rules']) {
    for (const list of [[], [{id:'example',version:1}]]) {
      response = list;
      assert.deepEqual(await tools.get(name).execute({}), {items:list,count:list.length});
    }
  }
  response = {status:'error',http_status:422,diagnostics:[{code:'SEMANTIC_CONFLICT',message:'bad units'}]};
  assert.deepEqual(await tools.get('flux_list_modules').execute({}), response);
  response = {protocol_version:'4.0',module_contract_version:'3.0'};
  assert.deepEqual(await tools.get('flux_dictionary_contract').execute({}), response);
  console.log('10 tool output checks passed: lists wrapped; objects and diagnostics preserved.');
})().catch(error => { console.error(error); process.exitCode=1; });
