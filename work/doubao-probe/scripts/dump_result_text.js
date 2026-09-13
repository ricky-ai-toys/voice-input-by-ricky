// Dump raw (non-desensitized) ASR result items.
//
// Site 0x1407156DC (rva 0x7156DC) is where the parsed result vector is walked
// to log "sami parsed result ...": [rbp+0x48] = begin, [rbp+0x50] = end,
// element size 64 bytes. Each element is inspected for embedded std::string
// values and terminated char* candidates.

const SITE_RVA = 0x7156DC;
const ELEM_SIZE = 64;

const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const DID_15 = '252163708395682';
const TOKEN_DID_SITE_RVA = 0x714893;
const didPtr = Memory.allocUtf8String(DID_15);

function isMostlyPrintable(s) {
  if (!s || s.length === 0) return false;
  let bad = 0;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (c < 0x20 && c !== 0x09 && c !== 0x0a) bad++;
  }
  return bad === 0;
}

function tryRead(addr, max) {
  try {
    return addr.readUtf8String(max);
  } catch (e) {
    return null;
  }
}

function dumpVector() {
  Interceptor.attach(Process.getModuleByName('ImeService.exe').base.add(SITE_RVA), {
    onEnter() {
      const begin = this.context.rbp.add(0x48).readPointer();
      const end = this.context.rbp.add(0x50).readPointer();
      const count = end.sub(begin).toInt32() / ELEM_SIZE;
      send({ kind: 'result-vector', count: count });
      for (let i = 0; i < count && i < 64; i++) {
        const item = begin.add(i * ELEM_SIZE);
        const fields = [];
        for (let off = 0; off < ELEM_SIZE; off += 8) {
          let v;
          try {
            v = item.add(off).readPointer();
          } catch (e) {
            continue;
          }
          fields.push({ off: off, ptr: v.toString() });
          // candidate char* at this slot
          const direct = tryRead(v, 256);
          if (direct && isMostlyPrintable(direct) && /[\u4e00-\u9fff]/.test(direct)) {
            send({ kind: 'text-charptr', index: i, off: off, text: direct });
          }
          // candidate MSVC std::string at this slot
          try {
            const size = item.add(off + 0x10).readU64().toNumber();
            const cap = item.add(off + 0x18).readU64().toNumber();
            if (size > 0 && size < 4096 && cap >= size) {
              const dataPtr = cap >= 16 ? item.add(off).readPointer() : item.add(off);
              const s = dataPtr.readUtf8String(size);
              if (s && /[\u4e00-\u9fff]/.test(s)) {
                send({ kind: 'text-stdstring', index: i, off: off, size: size, text: s });
              }
            }
          } catch (e) {
            /* not a std::string slot */
          }
        }
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

function hookGuardAndToken() {
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
  hookGuardAndToken();
  dumpVector();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
