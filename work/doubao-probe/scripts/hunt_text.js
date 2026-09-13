// Locate where the unredacted ASR text lives.
//
// Two probes:
//  1) the app logging entry (rva 0x914250) - dump pointer-ish arguments and look
//     for CJK payloads (if the logger receives real text, redaction happens later)
//  2) the "sami parsed result" site (rva 0x7156DC) - scan the stack frame for
//     CJK std::string / char* candidates

const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
const LOG_FN_RVA = 0x914250;
const PARSE_SITE_RVA = 0x7156DC;

const didPtr = Memory.allocUtf8String(DID_15);
const seen = new Set();

function hasCjk(s) {
  return !!s && /[\u3000-\u9fff\uff00-\uffef]/.test(s);
}

function probeString(p, max) {
  // UTF-16LE candidate (typical for Windows IME text)
  try {
    const w = p.readUtf16String(max || 200);
    if (w && w.length >= 2 && hasCjk(w)) return w;
  } catch (e) {
    /* ignore */
  }
  // char* candidate
  try {
    const s = p.readUtf8String(max || 200);
    if (s && s.length >= 2 && hasCjk(s)) return s;
  } catch (e) {
    /* ignore */
  }
  // std::wstring candidate: {ptr|buf, size@0x10, cap@0x18} counted in wchar_t
  try {
    const size = p.add(0x10).readU64().toNumber();
    const cap = p.add(0x18).readU64().toNumber();
    if (size > 0 && size < 4096 && cap >= size && cap < 100000) {
      const dataPtr = cap >= 8 ? p.readPointer() : p;
      const w = dataPtr.readUtf16String(size);
      if (hasCjk(w)) return w;
    }
  } catch (e) {
    /* ignore */
  }
  // MSVC std::string candidate: {ptr|buf, size@0x10, cap@0x18}
  try {
    const size = p.add(0x10).readU64().toNumber();
    const cap = p.add(0x18).readU64().toNumber();
    if (size > 0 && size < 4096 && cap >= size && cap < 100000) {
      const dataPtr = cap >= 16 ? p.readPointer() : p;
      const s = dataPtr.readUtf8String(size);
      if (hasCjk(s)) return s;
    }
  } catch (e) {
    /* ignore */
  }
  return null;
}

function report(kind, text, extra) {
  const key = kind + '|' + text;
  if (seen.has(key)) return;
  seen.add(key);
  send({ kind: kind, text: text, extra: extra });
}

function hookLogger() {
  Interceptor.attach(Process.getModuleByName('ImeService.exe').base.add(LOG_FN_RVA), {
    onEnter(args) {
      for (let i = 0; i < 4; i++) {
        const s = probeString(args[i], 400);
        if (s) report('logger-arg', s, 'arg' + i);
      }
      for (let off = 0x20; off <= 0x60; off += 8) {
        try {
          const s = probeString(this.context.rsp.add(off).readPointer(), 400);
          if (s) report('logger-stack', s, 'rsp+' + off.toString(16));
        } catch (e) {
          /* ignore */
        }
      }
    }
  });
}

function hookParseSite() {
  Interceptor.attach(Process.getModuleByName('ImeService.exe').base.add(PARSE_SITE_RVA), {
    onEnter() {
      const rbp = this.context.rbp;
      for (let off = -0x300; off <= 0x100; off += 8) {
        let p;
        try {
          p = rbp.add(off).readPointer();
        } catch (e) {
          continue;
        }
        const s = probeString(p, 400);
        if (s) report('parse-stack', s, 'rbp' + (off >= 0 ? '+' : '') + off.toString(16));
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

function hookStdout() {
  const k32 = Process.getModuleByName('kernel32.dll');
  const addr = k32.getExportByName('WriteFile');
  Interceptor.attach(addr, {
    onEnter(args) {
      try {
        const len = args[2].toInt32();
        if (len <= 0 || len > 65536) return;
        const t = args[1].readUtf8String(len);
        if (t && t.trim() && /sami test result|SessionStarted/.test(t)) {
          send({ kind: 'stdout', text: t.trim().slice(0, 300) });
        }
      } catch (e) {
        /* ignore */
      }
    }
  });
}

try {
  hookStdout();
  hookPatches();
  hookLogger();
  hookParseSite();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
