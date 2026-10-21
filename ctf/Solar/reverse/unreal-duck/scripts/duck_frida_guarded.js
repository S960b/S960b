'use strict';
// UnrealDuck CTF: diagnostic-first, bounded local hook.
// Run using duck_frida_guarded.py or: frida -p <SHIPPING_PID> -l duck_frida_guarded.js
const TARGET = 'unrealduckclient-win64-shipping.exe';
const RVA = 0x04394600;
const SIGNATURE = '40 55 56 57 48 83 EC 40 F6 81 48 03 00 00 02 49 8B F0 48 8B EA 48 8B F9';
const EXPECTED = SIGNATURE.split(' ').map(x => parseInt(x, 16));
const SCAN_FALLBACK = false; // Python --scan enables this explicitly.
const MAX_PAYLOAD = 32 * 1024 * 1024;
let installed = false;
let lastDiagnostic = '';
let scanAttempted = false;
let checks = 0;
function emit(type, fields) { send(Object.assign({type: type, pid: Process.id}, fields || {})); }
function diagnose(msg, extra) {
  const key = msg + ':' + JSON.stringify(extra || {});
  if (key !== lastDiagnostic || checks % 15 === 0) {
    lastDiagnostic = key;
    emit('diagnostic', Object.assign({message: msg}, extra || {}));
  }
}
function hexBytes(address, count) {
  const data = address.readVolatile(count); // safe when a read races with unmapping
  if (data === null || data === undefined) throw new Error('readVolatile failed');
  return Array.from(new Uint8Array(data));
}
function hexString(bytes) { return bytes.map(x => x.toString(16).padStart(2, '0')).join(' '); }
function isExpected(bytes) { return EXPECTED.every((x, i) => bytes[i] === x); }
function rangeFor(address, length) {
  const first = Process.findRangeByAddress(address);
  const last = Process.findRangeByAddress(address.add(length - 1));
  if (!first || !last || first.protection.indexOf('r') < 0 ||
      last.protection.indexOf('r') < 0) return null;
  return {first, last};
}
function tryHook(module, address, mode) {
  const rr = rangeFor(address, EXPECTED.length);
  if (!rr) {
    diagnose('RVA address is unmapped/unreadable', {module:module.name, base:String(module.base),
      size:module.size, address:String(address), rva:'0x' + RVA.toString(16)});
    return false;
  }
  if (rr.first.protection.indexOf('x') < 0 || rr.last.protection.indexOf('x') < 0) {
    diagnose('Target address is not executable', {address:String(address), protection:rr.first.protection});
    return false;
  }
  let bytes;
  try { bytes = hexBytes(address, EXPECTED.length); }
  catch (err) { diagnose('Safe memory read failed', {address:String(address), error:String(err)}); return false; }
  if (!isExpected(bytes)) {
    diagnose('Signature mismatch: wrong build/RVA (hook NOT installed)',
      {address:String(address), found:hexString(bytes), expected:SIGNATURE});
    return false;
  }
  try {
    Interceptor.attach(address, {
      onEnter(args) {
        try {
          const title = args[1], arr = args[2]; // x64: RDX and R8 are refs to UE containers.
          if (title.isNull() || arr.isNull()) throw new Error('Missing args');
          const tp = title.readPointer();
          const tn = title.add(Process.pointerSize).readS32();
          const tmax = title.add(Process.pointerSize + 4).readS32();
          let titleText = '';
          if (tn < 0 || tn > 4096 || tmax < tn) throw new Error('Bad FString bounds');
          if (tn > 0 && !tp.isNull()) titleText = tp.readUtf16String(tn - 1);
          const bp = arr.readPointer();
          const n = arr.add(Process.pointerSize).readS32();
          const max = arr.add(Process.pointerSize + 4).readS32();
          if (n < 0 || max < n || n > MAX_PAYLOAD || (n && bp.isNull())) {
            emit('error', {message:'Invalid image TArray bounds', title:titleText, bytes:n, capacity:max});
            return;
          }
          const metadata = {type:'payload', title:titleText, bytes:n, capacity:max};
          let contents;
          if (n > 0) {
            contents = bp.readVolatile(n);
            if (contents === null) throw new Error('Payload not currently readable');
          } else contents = new ArrayBuffer(0);
          send(metadata, contents);
        } catch(e) { emit('error', {message:'Payload decode failed', error:String(e)}); }
      }
    });
    installed = true;
    emit('ready', {module:module.name, path:module.path, base:String(module.base),
       moduleSize:module.size, rva:'0x'+RVA.toString(16), handler:String(address),
       method:mode, signature:hexString(bytes)});
    return true;
  } catch(e) { diagnose('Interceptor.attach failed', {address:String(address), error:String(e)}); return false; }
}
function scanSameModule(module) {
  if (scanAttempted) return;
  scanAttempted = true;
  emit('diagnostic', {message:'Opt-in scan of executable ranges within exact Shipping module started'});
  const matches = [];
  try {
    for (const r of module.enumerateRanges('r-x')) {
      // Only search regions belonging to the module's VA bounds.
      const start = r.base.compare(module.base) < 0 ? module.base : r.base;
      const end = r.base.add(r.size);
      const moduleEnd = module.base.add(module.size);
      const effectiveEnd = end.compare(moduleEnd) > 0 ? moduleEnd : end;
      if (effectiveEnd.compare(start) <= 0) continue;
      const count = effectiveEnd.sub(start).toInt32();
      if (count <= 0) continue;
      for (const hit of Memory.scanSync(start, count, SIGNATURE)) matches.push(hit.address);
    }
    emit('scan_results', {hits:matches.map(String)});
    if (matches.length === 1) tryHook(module, matches[0], 'unique full-signature scan');
    else diagnose('Scan did not find exactly one candidate; refusing to hook', {hitCount:matches.length});
  } catch(e) { diagnose('Scan failed', {error:String(e)}); }
}
function poll() {
  if (installed) return;
  checks++;
  let modules, main;
  try {
    modules = Process.enumerateModules();
    main = Process.mainModule;
  } catch(e) { diagnose('Module enumeration failed', {error:String(e)}); return; }
  if (checks === 1) emit('process', {arch:Process.arch,
     main:main ? {name:main.name,path:main.path,base:String(main.base),size:main.size} : null});
  const candidates = modules.filter(m => m.name.toLowerCase().includes('unrealduck'));
  if (checks === 1 || checks % 15 === 0) {
    emit('modules', {candidates:candidates.map(m=>({name:m.name,path:m.path,
       base:String(m.base),size:m.size, rvaFits:RVA + EXPECTED.length <= m.size}))});
  }
  const module = candidates.find(m => m.name.toLowerCase() === TARGET);
  if (!module) { diagnose('Shipping module NOT present in this process; check PID (launcher?)',
     {found:candidates.map(m=>m.name)}); return; }
  if (Process.arch !== 'x64') { diagnose('Process is not x64', {arch:Process.arch}); return; }
  if (RVA + EXPECTED.length > module.size) {
    diagnose('RVA OUTSIDE module image: wrong module or different build',
      {module:module.name,path:module.path,base:String(module.base),size:module.size,
       requiredImageSize:RVA+EXPECTED.length});
    if (SCAN_FALLBACK) scanSameModule(module);
    return;
  }
  const address = module.base.add(RVA);
  if (!tryHook(module, address, 'fixed RVA') && SCAN_FALLBACK) scanSameModule(module);
}
poll();
const ticker = setInterval(() => { if (installed) clearInterval(ticker); else poll(); }, 1000);
