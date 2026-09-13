import time, ctypes, ctypes.wintypes as wt, keyboard

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]
class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 24)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]

u32 = ctypes.windll.user32
def send(vk, scan, flags):
    inp = INPUT(type=1)
    inp.ki.wVk = vk; inp.ki.wScan = scan; inp.ki.dwFlags = flags
    u32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

KEYEVENTF_EXTENDEDKEY=0x0001; KEYEVENTF_KEYUP=0x0002
events=[]
h=keyboard.hook(lambda e: events.append((e.name, e.scan_code, e.event_type, getattr(e,'is_keypad',None))))
time.sleep(0.4)
print('--- SendInput right alt ---')
send(0xA5, 0x38, KEYEVENTF_EXTENDEDKEY); time.sleep(0.4)
send(0xA5, 0x38, KEYEVENTF_EXTENDEDKEY|KEYEVENTF_KEYUP)
time.sleep(0.4)
print('events:', events)
keyboard.unhook(h)
