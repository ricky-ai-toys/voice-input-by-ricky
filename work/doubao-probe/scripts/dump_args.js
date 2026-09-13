const mod = Process.getModuleByName('ImeService.exe');
const DID_15 = '252163708395682';
const didPtr = Memory.allocUtf8String(DID_15);
Interceptor.attach(mod.base.add(0x717762), { onEnter() { const f=this.context.rdi.add(0x290); try { if (f.readPointer().isNull()) f.writePointer(didPtr); } catch(e){} } });
Interceptor.attach(mod.base.add(0x714893), { onEnter() { const s=this.context.r14; let n=-1; try { n=s.add(0x10).readU64().toNumber(); } catch(e){ return; } if(n!==0) return; for(let i=0;i<DID_15.length;i++) s.add(i).writeU8(DID_15.charCodeAt(i)); s.add(DID_15.length).writeU8(0); s.add(0x10).writeU64(DID_15.length); s.add(0x18).writeU64(15); } });
Interceptor.attach(mod.base.add(0x715AE0), {
  onEnter(args) {
    for (let i = 0; i < 2; i++) {
      const p = args[i];
      let asStr = null, viaPtr = null;
      try { asStr = p.readUtf8String(120); } catch (e) {}
      try { const q = p.readPointer(); viaPtr = q.readUtf8String(120); } catch (e) {}
      send({ kind: 'arg', index: i, ptr: p.toString(), direct: asStr, indirect: viaPtr });
    }
  }
});
const k32 = Process.getModuleByName('kernel32.dll');
for (const name of ['WriteFile','WriteConsoleW']) {
  const addr = k32.getExportByName(name);
  Interceptor.attach(addr, { onEnter(a) { try { if (name==='WriteConsoleW') { const t=a[1].readUtf16String(); if(t) send({kind:'stdout',text:t.slice(0,4000)}); return; } const len=a[2].toInt32(); if(len<=0||len>65536) return; let t; try { t=a[1].readUtf8String(len); } catch(e){ t=a[1].readUtf16String(len/2);} if(t&&t.trim()) send({kind:'stdout',text:t.slice(0,4000)}); } catch(e){} } });
}
