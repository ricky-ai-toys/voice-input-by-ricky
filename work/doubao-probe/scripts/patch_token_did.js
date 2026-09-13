// Inject a device id into the ASR token at the point where the token JSON is
// assembled, so samicore accepts it ({"device_id":"...","aid":"685343"}).
//
// Site 0x140714893 (rva 0x714893): "mov rdx, r14" right after the token prefix
// literals are appended; r14 points at the std::string holding the DID.
// The string is empty in the --test-sami path, so we write the id into its
// inline SSO buffer (capacity 15 -> 15 chars, no allocation, destructor is a
// no-op because the string stays in SSO mode).

const TOKEN_DID_SITE_RVA = 0x714893;
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const DID_15 = '252163708395682'; // 15 chars: fits MSVC std::string SSO buffer

const didPtr = Memory.allocUtf8String(DID_15);

function hookTokenDid() {
  const mod = Process.getModuleByName('ImeService.exe');
  const site = mod.base.add(TOKEN_DID_SITE_RVA);
  Interceptor.attach(site, {
    onEnter() {
      const s = this.context.r14;
      let size = -1;
      try {
        size = s.add(0x10).readU64().toNumber();
      } catch (e) {
        send({ kind: 'token-did-error', message: e.message });
        return;
      }
      send({ kind: 'token-did-site', size: size, ptr: s.toString() });
      if (size !== 0) return;
      for (let i = 0; i < DID_15.length; i++) {
        s.add(i).writeU8(DID_15.charCodeAt(i));
      }
      s.add(DID_15.length).writeU8(0);
      s.add(0x10).writeU64(DID_15.length); // size
      s.add(0x18).writeU64(15);            // capacity (max for SSO)
      send({ kind: 'token-did-patched', value: DID_15 });
    }
  });
}

function hookGuard() {
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

try {
  hookStdout();
  hookTokenDid();
  hookGuard();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
