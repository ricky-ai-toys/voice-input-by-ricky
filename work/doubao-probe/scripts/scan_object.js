// Scan the SamiAsrClientWin object for the parsed result text.
//
// Hooks the "sami test result valid=%d full_vad=%d bytes=%d" log site, takes the
// object pointer from rdi/rcx, and reports std::string-like fields plus any raw
// UTF-8 CJK runs found inside the object.

const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
// function that logs "sami test result valid=%d full_vad=%d bytes=%d";
// rdx points at the parsed result struct
const RESULT_LOG_SITE_RVA = 0x70FA50;

const didPtr = Memory.allocUtf8String(DID_15);
const seen = new Set();

function report(kind, value, extra) {
  const key = kind + '|' + value;
  if (seen.has(key)) return;
  seen.add(key);
  send({ kind: kind, text: value, extra: extra });
}

function scanObject(obj, tag) {
  for (let off = 0; off < 0x1000; off += 8) {
    let size, cap;
    try {
      size = obj.add(off + 0x10).readU64().toNumber();
      cap = obj.add(off + 0x18).readU64().toNumber();
    } catch (e) {
      continue;
    }
    if (size < 1 || size > 4096 || cap < size || cap > 100000) continue;
    let dataPtr;
    try {
      dataPtr = cap >= 16 ? obj.add(off).readPointer() : obj.add(off);
    } catch (e) {
      continue;
    }
    let s = null;
    try {
      s = dataPtr.readUtf8String(size);
    } catch (e) {
      continue;
    }
    if (!s) continue;
    const printable = [...s].every((ch) => {
      const c = ch.codePointAt(0);
      return c >= 0x20 || c === 9 || c === 10;
    });
    if (printable && s.length >= 3) {
      report('obj-string', s, tag + ' +0x' + off.toString(16));
    }
  }
  // raw CJK byte runs
  for (let off = 0; off < 0x1000; off += 1) {
    let b;
    try {
      b = obj.add(off).readU8();
    } catch (e) {
      break;
    }
    if (b < 0xe4 || b > 0xe9) continue;
    try {
      const s = obj.add(off).readUtf8String(180);
      if (s && /[\u4e00-\u9fff]{2,}/.test(s)) {
        report('obj-cjk', s, tag + ' +0x' + off.toString(16));
      }
    } catch (e) {
      /* ignore */
    }
  }
}

function hookResultLog() {
  const mod = Process.getModuleByName('ImeService.exe');
  Interceptor.attach(mod.base.add(RESULT_LOG_SITE_RVA), {
    onEnter() {
      const candidates = [this.context.rdx, this.context.rdi, this.context.rcx, this.context.rbx];
      for (let i = 0; i < candidates.length; i++) {
        const p = candidates[i];
        if (!p || p.isNull()) continue;
        try {
          p.readU64();
        } catch (e) {
          continue;
        }
        scanObject(p, 'cand' + i);
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
  hookPatches();
  hookResultLog();
  send({ kind: 'ready' });
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
