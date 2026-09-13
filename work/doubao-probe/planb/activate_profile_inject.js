// Activate the Doubao input profile inside a target process (process-wide).
//
// Used as: frida script loaded by activate_profile_inject.py into e.g. notepad.exe.
// After this, real keystrokes to that process are routed to the IME and its DLLs load.

const CLSID_TF_InputProcessorProfiles = [
    0x33C53A50, 0xF456, 0x4884, [0xB0, 0x49, 0x85, 0xFD, 0x64, 0x3E, 0xCF, 0xED]];
const IID_ITfInputProcessorProfileMgr = [
    0x71C6E74C, 0x0F28, 0x11D8, [0xA8, 0x2A, 0x00, 0x06, 0x5B, 0x84, 0x43, 0x5C]];
const CLSID_Doubao = [
    0x9D2B2E2B, 0x3C93, 0x4D2F, [0x9D, 0x35, 0x6E, 0xEB, 0x85, 0xF0, 0xD2, 0xB0]];
const GUID_DoubaoProfile = [
    0x2B4D4B3A, 0x4D4F, 0x4C0A, [0x8E, 0x66, 0x7F, 0x77, 0x1A, 0x2B, 0x9C, 0x10]];

const LANGID_CHS = 0x0804;
const TF_PROFILETYPE_INPUTPROCESSOR = 1;
const TF_IPPMF_FORPROCESS = 0x10000000;

function guidBytes(spec) {
    const mem = Memory.alloc(16);
    mem.writeU32(spec[0]);
    mem.add(4).writeU16(spec[1]);
    mem.add(6).writeU16(spec[2]);
    for (let i = 0; i < 8; i++) mem.add(8 + i).writeU8(spec[3][i]);
    return mem;
}

function main() {
    const ole32 = Process.getModuleByName('ole32.dll');
    const coCreate = new NativeFunction(ole32.getExportByName('CoCreateInstance'), 'int',
        ['pointer', 'pointer', 'uint32', 'pointer', 'pointer']);

    const clsid = guidBytes(CLSID_TF_InputProcessorProfiles);
    const iid = guidBytes(IID_ITfInputProcessorProfileMgr);
    const out = Memory.alloc(Process.pointerSize);
    const hr = coCreate(clsid, ptr(0), 1, iid, out);
    const mgr = out.readPointer();
    send({ kind: 'cocreate', hr: hr, ptr: mgr.toString() });
    if (hr !== 0 || mgr.isNull()) return;

    const vtable = mgr.readPointer();
    const activate = new NativeFunction(vtable.add(3 * 8).readPointer(), 'int',
        ['pointer', 'uint32', 'uint16', 'pointer', 'pointer', 'pointer', 'uint32']);
    const dobjClsid = guidBytes(CLSID_Doubao);
    const dprofile = guidBytes(GUID_DoubaoProfile);
    const hr2 = activate(mgr, TF_PROFILETYPE_INPUTPROCESSOR, LANGID_CHS, dobjClsid, dprofile,
                         ptr(0), TF_IPPMF_FORPROCESS);
    send({ kind: 'activate', hr: hr2 });
}

try {
    main();
} catch (e) {
    send({ kind: 'error', message: e.message, stack: e.stack });
}
