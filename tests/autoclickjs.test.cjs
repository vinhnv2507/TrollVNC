const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.join(__dirname, '..');
const catalog = JSON.parse(fs.readFileSync(path.join(root, 'app/TrollVNC/TrollVNC/AutoClickJS.json'), 'utf8'));
const native = fs.readFileSync(path.join(root, 'src/trollvncserver.mm'), 'utf8');
const names = new Set([...native.matchAll(/ctx\[@"([A-Za-z]+)"\]\s*=/g)].map(m => m[1]));
const prelude = new Set([...native.matchAll(/function ([A-Za-z]+)\(/g)].map(m => m[1]));
const js = new Set(['while', 'for', 'if', 'JSON']);
const entries = new Set(catalog.commands.map(c => c.name));
assert.equal(entries.size, catalog.commands.length, 'No duplicate API entries');
for (const name of names) assert.ok(entries.has(name), `Missing documentation for native API ${name}`);
for (const name of prelude) assert.ok(entries.has(name), `Missing documentation for helper ${name}`);
for (const row of catalog.commands) {
    assert.ok(names.has(row.name) || prelude.has(row.name) || js.has(row.name), `Unsupported API ${row.name}`);
    assert.ok(row.description && row.signature && row.group && row.code);
}
for (const row of [...catalog.commands, ...catalog.templates]) new vm.Script(row.code, {filename: row.name+'.js'});

// Evaluate every example against the actual documented API surface. No network,
// screen actions or filesystem writes are performed by these test doubles.
for (const row of [...catalog.commands, ...catalog.templates]) {
    let elapsed = 0, sleeps = 0;
    const context = {JSON, Math, Number};
    for (const name of [...names, ...prelude]) context[name] = () => null;
    Object.assign(context, {
        now: () => elapsed, sleep: s => {assert.ok(s >= 0); elapsed += s*1000; if (++sleeps > 30) throw new Error('bounded-test-stop');},
        stop: () => {throw new Error('bounded-test-stop');},
        ocr: () => 'no timer', readFile: () => '{}', getVar: (k,d) => d,
        screenWidth: () => 750, screenHeight: () => 1334,
        random: (a,b) => (a+b)/2, repeat: (n,fn) => {for(let i=0;i<n;i++)fn(i)},
        retry: (n,fn) => {for(let i=0;i<n;i++)if(fn(i))return true;return false},
    });
    try { vm.runInNewContext(row.code, context, {timeout: 1000}); }
    catch (e) { if (e.message !== 'bounded-test-stop') throw new Error(`${row.name}: ${e.message}`); }
}

const timer = catalog.templates.find(x => x.name === 'Đọc MM:SS và tính chờ');
for (const [text,seconds,ocrMillis] of [['Xem 01:08 để nhận',68,4500], ['00:29',29,3200],
    ['00: 29',29,2900], ['8：15',495,500], ['00:00',0,500], ['12:60',null,100], ['123:45',null,100], ['22 xu',null,100]]) {
    let elapsed=0, samples=[], taps=0;
    vm.runInNewContext(timer.code, {now:()=>elapsed, sleep:s=>{elapsed+=s*1000},
        ocr:()=>{samples.push(elapsed);elapsed+=ocrMillis;return text}, log:()=>{}, tap:()=>{taps++}}, {timeout:1000});
    assert.equal(taps,0,'The MM:SS template never taps unverified coordinates');
    assert.equal(elapsed, samples[0] + Math.max(seconds===null?0:seconds*1000, ocrMillis), text);
}
console.log(`AutoClickJS: ${entries.size} API/control entries and ${catalog.templates.length} examples parsed and executed; native API coverage and MM:SS deadlines passed`);
