import ctypes, re, capstone, os
runtime = os.path.abspath('scratch')
os.add_dll_directory(runtime)
dll = ctypes.CDLL(os.path.join(runtime, 'rpc.dll'))
k32 = ctypes.windll.kernel32
k32.GetModuleHandleW.restype = ctypes.c_void_p
create = dll.CreateRpcClient; create.restype = ctypes.c_void_p; create.argtypes=[ctypes.c_char_p]
obj = create(b'\\\\.\\pipe\\ObricIme\\oime-serveR')
base = k32.GetModuleHandleW('rpc.dll')
vt = ctypes.c_void_p.from_address(obj).value
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
for i in range(12):
    fn = ctypes.c_void_p.from_address(vt + i*8).value
    if not fn: break
    code = ctypes.string_at(fn, 400)
    ops = []
    for insn in md.disasm(code, fn):
        m = re.search(r', (0x[0-9a-f]{1,4})$', insn.op_str)
        if m and insn.mnemonic in ('mov','push','cmp','or','and'):
            v = int(m.group(1), 16)
            if 1 <= v <= 0x60 and v not in ops:
                ops.append(v)
    print(f'vtable[{i:2d}] small constants: ' + ', '.join(hex(v) for v in ops[:10]))
