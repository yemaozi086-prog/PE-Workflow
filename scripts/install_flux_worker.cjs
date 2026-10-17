const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const root=path.resolve(__dirname,'..');
const source=path.join(root,'dsh/preset/flux-worker');
const destination=path.join(os.homedir(),'.dsh/.agent-presets/flux-worker');
fs.mkdirSync(destination,{recursive:true});fs.cpSync(source,destination,{recursive:true,force:true});
fs.writeFileSync(path.join(destination,'flux-root.txt'),root.replace(/\\/g,'/'),'utf8');
console.log('Installed dedicated flux-worker preset: '+destination+'; defaults unchanged.');
