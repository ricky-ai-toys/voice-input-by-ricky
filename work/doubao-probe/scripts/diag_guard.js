// Diagnostic: prove instrumentation works, capture stdout writes, and patch the
// AppLog DID guard in ImeService.exe.

const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const DID = '2521637083956824';

const didPtr = Memory.allocUtf8String(DID);
let wavSeen = false;

function hookWrites() {
  const k32 = Process.getModuleByName('kernel32.dll');
  for (const name of ['WriteFile', 'WriteConsoleA', 'WriteConsoleW']) {
    const addr = k32.findExportByName(name);
    if (!addr) continue;
    Interceptor.attach(addr, {
      onEnter(args) {
        try {
          if (name === 'WriteConsoleW') {
            const text = args[1].readUtf16String();
            if (text) send({ kind: 'stdout', text: text.slice(0, 4000) });
            return;
          }
          const buf = args[1];
          const len = args[2].toInt32();
          if (len <= 0 || len > 65536) return;
          let text;
          try {
            text = buf.readUtf8String(len);
          } catch (e) {
            text = buf.readUtf16String(len / 2);
          }
          if (text && text.trim().length) send({ kind: 'stdout', text: text.slice(0, 4000) });
        } catch (e) {
          /* ignore */
        }
      }
    });
  }
}

function hookGuard() {
  const mod = Process.getModuleByName('ImeService.exe');
  const guard = mod.base.add(GUARD_RVA);
  send({ kind: 'init', base: mod.base.toString(), guard: guard.toString() });
  Interceptor.attach(guard, {
    onEnter() {
      const field = this.context.rdi.add(DID_FIELD_OFFSET);
      let cur = ptr(0);
      try {
        cur = field.readPointer();
      } catch (e) {
        cur = ptr(0);
      }
      send({ kind: 'guard-hit', didField: cur.toString(), rdi: this.context.rdi.toString() });
      if (cur.isNull()) {
        field.writePointer(didPtr);
        send({ kind: 'patched' });
      }
    }
  });
}

function hookFileOpen() {
  const k32 = Process.getModuleByName('kernel32.dll');
  const addr = k32.findExportByName('CreateFileW');
  if (!addr) return;
  Interceptor.attach(addr, {
    onEnter(args) {
      try {
        const name = args[0].readUtf16String();
        if (name && name.toLowerCase().endsWith('.wav')) {
          wavSeen = true;
          send({ kind: 'open-wav', name });
        }
      } catch (e) {
        /* ignore */
      }
    }
  });
}

try {
  // Report the command line the child actually received.
  try {
    const p = Process.getModuleByName('kernel32.dll').getExportByName('GetCommandLineW')
      || Module.getGlobalExportByName('GetCommandLineW');
    Interceptor.attach(p, {
      onLeave(retval) {
        try {
          send({ kind: 'cmdline', value: retval.readUtf16String() });
        } catch (e) {
          send({ kind: 'cmdline-error', message: e.message });
        }
      }
    });
  } catch (e) {
    send({ kind: 'cmdline-error', message: e.message });
  }
  hookWrites();
  hookFileOpen();
  hookGuard();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
