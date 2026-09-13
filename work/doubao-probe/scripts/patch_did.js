// Make the cached device id visible to the app before the ASR client is built.
//
// In the --test-sami path the app runs before the applog runtime publishes the
// cached DID, so the ASR token ends up as {"device_id":"","aid":"685343"} and
// samicore rejects it (ret 100008). Hook the applog SDK getter so the wrapper
// always sees the DID that is already cached in %APPDATA%\DoubaoIme\applog.

const DID = '2521637083956824';
const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;

const didPtr = Memory.allocUtf8String(DID);

function hookAppLogDid() {
  const mod = Process.getModuleByName('applogrs.dll');
  const addr = mod.getExportByName('AppLog_getDeviceId');
  Interceptor.attach(addr, {
    onLeave(retval) {
      let current = null;
      try {
        current = retval.readUtf8String();
      } catch (e) {
        current = null;
      }
      if (!current) {
        retval.replace(didPtr);
        send({ kind: 'did-injected' });
      } else if (current !== DID) {
        send({ kind: 'did-existing', value: current });
      }
    }
  });
  send({ kind: 'hook-applog', addr: addr.toString() });
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
      send({ kind: 'guard-hit', didField: cur.toString() });
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
  hookAppLogDid();
  hookGuard();
} catch (e) {
  send({ kind: 'error', message: e.message, stack: e.stack });
}
