#!/usr/bin/env node
/**
 * 把 Flux（pe-workflow）的 DSH 工具桥接接入本机 DSH —— 幂等、可重复执行。
 *
 * 只做两件事（与旧 aeep-dsh 的 dsh_register.js 不同，本脚本**不**注册 host 插件、
 * **不**改默认 preset，旧 pe-agent 与 settings.yaml 完全不动）：
 *   1. 安装 agent preset → <DshHome>/.agent-presets/flux/（含 flux-root.txt）
 *   2. 校验（文件齐全、root 精确无 BOM、插件 JS 语法、可选只读自检）
 *
 * 用法：
 *   node scripts/dsh_register_flux.js
 *   node scripts/dsh_register_flux.js --dry-run        # 只打印将要做的事
 *   node scripts/dsh_register_flux.js --check          # 只校验现状
 *   node scripts/dsh_register_flux.js --selfcheck      # 校验 + 对 8020 做只读自检
 *   node scripts/dsh_register_flux.js --set-default    # （可选）把默认 preset 设为 flux
 */

'use strict'

const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const { spawnSync } = require('node:child_process')

const PROJECT = path.resolve(__dirname, '..')
const PRESET_SRC = path.join(PROJECT, 'dsh', 'preset', 'flux')
const PRESET_ID = 'flux'
const PRESET_FILES = ['agent.cordis.yml', 'preset.yml', 'flux-tools-plugin.js', 'flux-root.txt']

function parseArgs(argv) {
  const opts = {
    dshHome: process.env.DSH_HOME || path.join(os.homedir(), '.dsh'),
    dryRun: false,
    check: false,
    selfcheck: false,
    setDefault: false,
  }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    const next = () => {
      const value = argv[i + 1]
      if (value === undefined) throw new Error(`${arg} 需要一个值`)
      i += 1
      return value
    }
    switch (arg) {
      case '--dsh-home': opts.dshHome = next(); break
      case '--dry-run': opts.dryRun = true; break
      case '--check': opts.check = true; break
      case '--selfcheck': opts.selfcheck = true; break
      case '--set-default': opts.setDefault = true; break
      case '--help': case '-h': opts.help = true; break
      default: throw new Error(`未知参数 ${arg}（--help 看用法）`)
    }
  }
  opts.dshHome = path.resolve(opts.dshHome)
  return opts
}

const HELP = `把 Flux（pe-workflow）工具桥接接入本机 DSH（幂等，不动 pe-agent / 不改默认 preset）
  --dsh-home <path>   DSH 主目录，默认 %USERPROFILE%\\.dsh
  --dry-run           只打印将要做的事
  --check             只校验现状，不写任何文件
  --selfcheck         校验 + 对 127.0.0.1:8020 做只读自检（不注册/不替换模块）
  --set-default       （可选）把默认 preset 设为 flux；默认不改 settings.yaml
`

const problems = []
const evidence = []
let changed = 0

function ok(text) { evidence.push(`  [OK]   ${text}`) }
function fail(text) { problems.push(text); evidence.push(`  [FAIL] ${text}`) }
function note(text) { evidence.push(`  ..     ${text}`) }

function writeText(file, text, dryRun) {
  if (dryRun) { note(`将写入 ${file}`); return }
  if (readIfExists(file) === text) return
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, text, { encoding: 'utf8' })
  changed += 1
}

function readIfExists(file) {
  try { return readTextNoBom(file) } catch { return undefined }
}

function readTextNoBom(file) {
  const raw = fs.readFileSync(file)
  const text = raw.toString('utf8')
  return text.charCodeAt(0) === 0xFEFF ? text.slice(1) : text
}

function installPreset(opts) {
  if (!fs.existsSync(PRESET_SRC)) throw new Error(`缺少 preset 源目录 ${PRESET_SRC}`)
  const dst = path.join(opts.dshHome, '.agent-presets', PRESET_ID)
  if (!opts.dryRun) {
    fs.mkdirSync(dst, { recursive: true })
    fs.cpSync(PRESET_SRC, dst, { recursive: true, force: true })
  }
  const rootText = PROJECT.replace(/\\/g, '/')
  writeText(path.join(dst, 'flux-root.txt'), rootText, opts.dryRun)
  if (opts.dryRun) note(`将同步 preset → ${dst}`)
  else ok(`preset 已安装 → ${dst}`)
  ok(`flux-root.txt = ${rootText}`)
  return dst
}

function setDefaultPreset(settingsFile, presetId, dryRun) {
  const original = readIfExists(settingsFile) || ''
  const lines = original.split(/\r?\n/)
  const start = lines.findIndex(line => /^agent-presets:\s*$/.test(line))
  let result
  if (start === -1) {
    const base = original === '' || original.endsWith('\n') ? original : `${original}\n`
    result = `${base}agent-presets:\n  default: ${presetId}\n`
  } else {
    let end = start + 1
    while (end < lines.length && (/^\s*$/.test(lines[end]) || /^\s/.test(lines[end]))) end += 1
    const block = lines.slice(start + 1, end)
    const defaultIndex = block.findIndex(line => /^\s+default:\s*/.test(line))
    if (defaultIndex === -1) block.unshift(`  default: ${presetId}`)
    else block[defaultIndex] = `  default: ${presetId}`
    result = [...lines.slice(0, start + 1), ...block, ...lines.slice(end)].join('\n')
  }
  writeText(settingsFile, result, dryRun)
  ok(`settings.yaml 默认 preset = ${presetId}（新会话自动生效；运行中的会话不变）`)
}

function verify(opts, presetDir) {
  for (const file of PRESET_FILES) {
    if (fs.existsSync(path.join(presetDir, file))) ok(`preset 文件存在：${file}`)
    else fail(`preset 缺少文件：${path.join(presetDir, file)}`)
  }
  const rootFile = path.join(presetDir, 'flux-root.txt')
  const rootText = readIfExists(rootFile)
  if (rootText === PROJECT.replace(/\\/g, '/')) ok('flux-root.txt 指向本工程且内容精确')
  else fail(`flux-root.txt 内容不符：${JSON.stringify(rootText)}`)
  const bytes = fs.existsSync(rootFile) ? fs.readFileSync(rootFile) : Buffer.alloc(0)
  if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) fail('flux-root.txt 含 BOM（会让插件路径解析失败）')
  else ok('flux-root.txt 无 BOM')

  // 插件 JS 语法校验（不执行、不加载 DSH）：node --check
  const plugin = path.join(presetDir, 'flux-tools-plugin.js')
  if (fs.existsSync(plugin)) {
    const r = spawnSync(process.execPath, ['--check', plugin], { encoding: 'utf8' })
    if (r.status === 0) ok('flux-tools-plugin.js 语法校验通过（node --check）')
    else fail(`flux-tools-plugin.js 语法错误：${(r.stderr || '').trim().slice(0, 400)}`)
  }

  // 桥接客户端存在性 + 白名单可打印
  const client = path.join(PROJECT, 'bridge', 'flux_client.py')
  if (fs.existsSync(client)) ok(`桥接客户端存在：${client}`)
  else fail(`缺少桥接客户端：${client}`)
}

function runSelfcheck() {
  const py = fs.existsSync(path.join(PROJECT,'runtime','python.exe')) ? path.join(PROJECT,'runtime','python.exe') : path.join(PROJECT, '.venv', 'Scripts', 'python.exe')
  const client = path.join(PROJECT, 'bridge', 'flux_client.py')
  if (!fs.existsSync(py)) { fail(`缺少 venv python：${py}`); return null }
  if (!fs.existsSync(client)) { fail(`缺少桥接客户端：${client}`); return null }
  const r = spawnSync(py, [client, '--selfcheck'], { encoding: 'utf8', cwd: PROJECT, timeout: 180000 })
  if (r.status !== 0) { fail(`只读自检执行失败：${(r.stderr || '').trim().slice(0, 400)}`); return null }
  let parsed = null
  try { parsed = JSON.parse(r.stdout) } catch (e) { fail(`只读自检输出非 JSON：${String(r.stdout || '').slice(0, 200)}`); return null }
  ok(`只读自检执行完成：${parsed.check_count} 项检查（service=${parsed.service}, read_only=${parsed.read_only}）`)
  return parsed
}

function section(title) { evidence.push(`\n${title}`) }

function report(opts) {
  process.stdout.write(`${evidence.join('\n')}\n`)
  process.stdout.write('\n' + '-'.repeat(70) + '\n')
  if (problems.length > 0) {
    process.stdout.write(`接入未完成：${problems.length} 项失败\n`)
    for (const problem of problems) process.stdout.write(`  - ${problem}\n`)
    return 1
  }
  if (opts.dryRun) {
    process.stdout.write('dry-run 通过：上面的动作都还没执行。去掉 --dry-run 即真正接入。\n')
    return 0
  }
  process.stdout.write(`接入完成${changed > 0 ? `（改动 ${changed} 处文件）` : '（无需改动，已是目标状态）'}。\n`)
  process.stdout.write('\n后续：\n')
  process.stdout.write('  1. 新建会话（或切换 preset 到 "Flux 平台（PE Workflow）"）—— agent 面工具按 preset 在会话创建时装载；运行中的会话不变\n')
  process.stdout.write('  2. 用 flux_health / flux_dictionary_contract / flux_list_modules 验证工具可用\n')
  process.stdout.write('  3. 旧 pe-agent 与默认 preset 均未被本脚本改动\n')
  return 0
}

function main() {
  const opts = parseArgs(process.argv.slice(2))
  if (opts.help) { process.stdout.write(HELP); return 0 }
  if (!fs.existsSync(PRESET_SRC)) throw new Error(`缺少 preset 源目录 ${PRESET_SRC}`)

  process.stdout.write('='.repeat(70) + '\n')
  process.stdout.write(`Flux 工具桥接接入 DSH${opts.dryRun ? '（dry-run，不写盘）' : opts.check ? '（只校验）' : ''}\n`)
  process.stdout.write('='.repeat(70) + '\n')
  process.stdout.write(`  工程根  : ${PROJECT}\n`)
  process.stdout.write(`  DSH 目录: ${opts.dshHome}\n\n`)

  const presetDir = path.join(opts.dshHome, '.agent-presets', PRESET_ID)

  if (opts.check) {
    section('校验现状')
    verify(opts, presetDir)
    if (opts.selfcheck) { section('只读自检（127.0.0.1:8020）'); runSelfcheck() }
    return report(opts)
  }

  section('[1/2] 安装 agent preset')
  installPreset(opts)

  section('[2/2] 校验')
  if (opts.dryRun) note('dry-run：跳过校验')
  else {
    verify(opts, presetDir)
    if (opts.selfcheck) { section('只读自检（127.0.0.1:8020）'); runSelfcheck() }
    if (opts.setDefault) setDefaultPreset(path.join(opts.dshHome, 'settings.yaml'), PRESET_ID, opts.dryRun)
    else note('未加 --set-default：settings.yaml 默认 preset 保持不变（旧 pe-agent / 现有配置不动）')
  }
  return report(opts)
}

try {
  process.exit(main())
} catch (error) {
  process.stdout.write(`${evidence.join('\n')}\n`)
  process.stderr.write(`\n接入失败：${error.message}\n`)
  process.exit(2)
}
