// Force the AppLog device-id field in SamiAsrClientWin before the guard check.
//
// Guard site (ImeService.exe 0.9.0.0) at RVA 0x717762:
//   cmp qword ptr [rdi + 0x290], 0
//   jne <carry on with ASR>
//   ... else log "sami start skipped because AppLog did is not ready"
//
// The DID exists in the local ttnet cache (%APPDATA%\DoubaoIme\ttnet\tt_net_config.config)
// but the --test-sami path runs before the applog runtime publishes it.

const GUARD_RVA = 0x717762;
const DID_FIELD_OFFSET = 0x290;
const DID = '2521637083956824';

function main() {
  const mod = Process.getModuleByName('ImeService.exe');
  const didPtr = Memory.allocUtf8String(DID);
  const guard = mod.base.add(GUARD_RVA);

  send({ kind: 'init', module: mod.name, base: mod.base.toString(), guard: guard.toString(), did: DID });

  Interceptor.attach(guard, {
    onEnter() {
      const field = this.context.rdi.add(DID_FIELD_OFFSET);
      let current = null;
      try {
        current = field.readPointer();
      } catch (e) {
        current = null;
      }
      if (current === null || current.isNull()) {
        field.writePointer(didPtr);
        send({ kind: 'patched', rdi: this.context.rdi.toString() });
      } else {
        send({ kind: 'already-set', value: current.toString() });
      }
    }
  });
}

try {
  main();
} catch (e) {
  send({ kind: 'error', message: e.message });
}
