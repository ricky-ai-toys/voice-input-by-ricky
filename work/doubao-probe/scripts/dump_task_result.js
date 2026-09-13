// Dump the raw (non-desensitized) ASR task-result JSON.
//
// Function at rva 0x715AE0 takes (char* json, size_t len) - it logs
// "sami task result ..." and then frees the buffer, so this is the app-side
// entry point where the unredacted payload is still intact.

const RESULT_FN_RVA = 0x715AE0;
const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
const didPtr = Memory.allocUtf8String(DID_15);

function hookResult() {
  const mod = Process.getModuleByName('ImeService.exe');
  Interceptor.attach(mod.base.add(RESULT_FN_RVA), {
    onEnter(args) {
      try {
        const ptrArg = args[0];
        const len = args[1].toInt32();
        let text = null;
        if (ptrArg && !ptrArg.isNull()) {
          text = len > 0 && len < 100000
            ? ptrArg.readUtf8String(len)
            : ptrArg.readUtf8String(4096);
        }
        send({ kind: 'task-result', len: len, text: text });
      } catch (e) {
        send({ kind: 'task-result-error', message: e.message });
      }
    }
  });
}

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
  hookPatches();
  hookResult();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
