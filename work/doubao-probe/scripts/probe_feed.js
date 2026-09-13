// Find which register holds the fed chunk size at the "sami feed chunk bytes=%d" site.

const DID = '252163708395682';
const mod = Process.getModuleByName('ImeService.exe');
const didPtr = Memory.allocUtf8String(DID);

Interceptor.attach(mod.base.add(0x717762), {
  onEnter() {
    const f = this.context.rdi.add(0x290);
    try {
      if (f.readPointer().isNull()) f.writePointer(didPtr);
    } catch (e) {}
  }
});

Interceptor.attach(mod.base.add(0x714893), {
  onEnter() {
    const s = this.context.r14;
    let n = -1;
    try {
      n = s.add(0x10).readU64().toNumber();
    } catch (e) {
      return;
    }
    if (n !== 0) return;
    for (let i = 0; i < DID.length; i++) s.add(i).writeU8(DID.charCodeAt(i));
    s.add(DID.length).writeU8(0);
    s.add(0x10).writeU64(DID.length);
    s.add(0x18).writeU64(15);
  }
});

let hits = 0;
Interceptor.attach(mod.base.add(0x712CC0), {
  onEnter() {
    if (hits++ > 4) return;
    const c = this.context;
    send({
      kind: 'feed-site',
      rcx: c.rcx.toString(), rdx: c.rdx.toString(), r8: c.r8.toString(), r9: c.r9.toString(),
      r10: c.r10.toString(), r11: c.r11.toString(), rsi: c.rsi.toString(), rdi: c.rdi.toString(),
      rbx: c.rbx.toString(), r12: c.r12.toString(), r13: c.r13.toString(), r14: c.r14.toString(),
      r15: c.r15.toString(), rbp: c.rbp.toString(), rsp: c.rsp.toString(),
    });
  }
});
