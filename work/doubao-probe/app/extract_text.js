// Extract the unredacted ASR result JSON.
//
// The function at rva 0x70FA50 receives the parsed result struct in rdx; the
// first member is a std::string holding the raw response JSON, e.g.
//   {"results":[{"is_interim":true,"text":"<real text>"}], ...}
// We ship it back base64-encoded to avoid any console encoding mangling.

const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
const RESULT_FN_RVA = 0x70FA50;

const didPtr = Memory.allocUtf8String(DID_15);

function toBase64(bytes) {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < bytes.length ? bytes[i + 1] : 0;
    const b2 = i + 2 < bytes.length ? bytes[i + 2] : 0;
    out += chars[b0 >> 2];
    out += chars[((b0 & 3) << 4) | (b1 >> 4)];
    out += i + 1 < bytes.length ? chars[((b1 & 15) << 2) | (b2 >> 6)] : '=';
    out += i + 2 < bytes.length ? chars[b2 & 63] : '=';
  }
  return out;
}

function readJson(objPtr) {
  let size, cap, dataPtr;
  try {
    size = objPtr.add(0x10).readU64().toNumber();
    cap = objPtr.add(0x18).readU64().toNumber();
  } catch (e) {
    return null;
  }
  if (size < 2 || size > 65536 || cap < size) return null;
  try {
    dataPtr = cap >= 16 ? objPtr.readPointer() : objPtr;
  } catch (e) {
    return null;
  }
  try {
    const bytes = dataPtr.readByteArray(size);
    return { text: dataPtr.readUtf8String(size), b64: toBase64(new Uint8Array(bytes)) };
  } catch (e) {
    return null;
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

function hookResult() {
  Interceptor.attach(Process.getModuleByName('ImeService.exe').base.add(RESULT_FN_RVA), {
    onEnter() {
      const json = readJson(this.context.rdx);
      if (json) send({ kind: 'json', text: json.text, b64: json.b64 });
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
        // the engine logs every frame it consumes; that is our read pointer
        if (t && t.indexOf('sami feed chunk bytes=') >= 0) {
          const m = /sami feed chunk bytes=(\d+)/.exec(t);
          if (m) send({ kind: 'feed', bytes: parseInt(m[1], 10) });
        }
        if (t && /sami test result valid=|SessionStarted|sami asr session stopped/.test(t)) {
          send({ kind: 'log', text: t.trim().slice(0, 240) });
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
  hookResult();
  send({ kind: 'ready' });
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
