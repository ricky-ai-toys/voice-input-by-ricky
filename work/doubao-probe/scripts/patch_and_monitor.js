// Same DID patches as patch_token_did.js, plus registry/file access monitoring
// so we can tell what the process actually depends on.

const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
const didPtr = Memory.allocUtf8String(DID_15);

function hookStdout() {
  const k32 = Process.getModuleByName('kernel32.dll');
  for (const name of ['WriteFile', 'WriteConsoleA', 'WriteConsoleW']) {
    const addr = k32.getExportByName(name);
    if (!addr) continue;
    Interceptor.attach(addr, {
      onEnter(args) {
        try {
          if (name === 'WriteConsoleW') {
            const t = args[1].readUtf16String();
            if (t) send({ kind: 'stdout', text: t.slice(0, 4000) });
            return;
          }
          const len = args[2].toInt32();
          if (len <= 0 || len > 65536) return;
          let t;
          try {
            t = args[1].readUtf8String(len);
          } catch (e) {
            t = args[1].readUtf16String(len / 2);
          }
          if (t && t.trim()) send({ kind: 'stdout', text: t.slice(0, 4000) });
        } catch (e) {
          /* ignore */
        }
      }
    });
  }
}

function hookRegistry() {
  const adv = Process.getModuleByName('advapi32.dll');
  const openKey = adv.getExportByName('RegOpenKeyExW');
  Interceptor.attach(openKey, {
    onEnter(args) {
      try {
        const hive = args[0];
        const sub = args[1].readUtf16String();
        send({ kind: 'reg-open', hive: hive.toString(), sub: sub });
      } catch (e) {
        /* ignore */
      }
    }
  });
}

function hookPatches() {
  const mod = Process.getModuleByName('ImeService.exe');
  Interceptor.attach(mod.base.add(GUARD_RVA), {
    onEnter() {
      const field = this.context.rdi.add(DID_FIELD_OFFSET);
      let cur = ptr(0);
      try {
        cur = field.readPointer();
      } catch (e) {
        cur = ptr(0);
      }
      if (cur.isNull()) field.writePointer(didPtr);
    }
  });
  Interceptor.attach(mod.base.add(TOKEN_DID_SITE_RVA), {
    onEnter() {
      const s = this.context.r14;
      let size = -1;
      try {
        size = s.add(0x10).readU64().toNumber();
      } catch (e) {
        return;
      }
      if (size !== 0) return;
      for (let i = 0; i < DID_15.length; i++) s.add(i).writeU8(DID_15.charCodeAt(i));
      s.add(DID_15.length).writeU8(0);
      s.add(0x10).writeU64(DID_15.length);
      s.add(0x18).writeU64(15);
    }
  });
}

try {
  hookStdout();
  hookRegistry();
  hookPatches();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
