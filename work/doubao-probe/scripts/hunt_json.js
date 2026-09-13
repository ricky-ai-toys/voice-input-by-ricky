// Look for the raw ASR result JSON (ASCII: contains "results"/"task_id").

const DID_15 = '252163708395682';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const TOKEN_DID_SITE_RVA = 0x714893;
const TASK_RESULT_FN_RVA = 0x715AE0;   // (a, b) -> logs "sami task result", frees a
const LOG_HELPER_RVA = 0x715260;       // (event, fmt, arg)

const didPtr = Memory.allocUtf8String(DID_15);
const seen = new Set();

function looksJson(s) {
  if (!s || s.length < 8) return false;
  return /"(results|task_id|is_interim|text|status_code)"/.test(s);
}

function tryRead(p) {
  const out = [];
  try {
    const s = p.readUtf8String(4096);
    if (looksJson(s)) out.push(s);
  } catch (e) {
    /* ignore */
  }
  try {
    const q = p.readPointer();
    const s = q.readUtf8String(4096);
    if (looksJson(s)) out.push(s);
  } catch (e) {
    /* ignore */
  }
  return out;
}

function report(tag, s) {
  const key = s.slice(0, 80);
  if (seen.has(key)) return;
  seen.add(key);
  send({ kind: 'json', tag: tag, text: s.slice(0, 1200) });
}

function hook(site, tag) {
  Interceptor.attach(Process.getModuleByName('ImeService.exe').base.add(site), {
    onEnter(args) {
      for (let i = 0; i < 4; i++) {
        for (const s of tryRead(args[i])) report(tag + '/arg' + i, s);
      }
      for (let off = 0x20; off <= 0x80; off += 8) {
        try {
          const p = this.context.rsp.add(off).readPointer();
          for (const s of tryRead(p)) report(tag + '/rsp+' + off.toString(16), s);
        } catch (e) {
          /* ignore */
        }
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
  hook(TASK_RESULT_FN_RVA, 'task-result');
  hook(LOG_HELPER_RVA, 'log-helper');
  send({ kind: 'ready' });
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
