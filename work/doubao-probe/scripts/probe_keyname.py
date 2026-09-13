import time, threading, keyboard
names = ['right alt','alt gr','right menu','alt']
for n in names:
    try:
        print(n, '->', keyboard.key_to_scan_codes(n))
    except Exception as e:
        print(n, 'ERR', e)
events=[]
h=keyboard.hook(lambda e: events.append((e.name, e.scan_code, e.event_type)))
time.sleep(0.5)
import ctypes
u=ctypes.windll.user32
u.keybd_event(0xA5,0x38,0x0001,None); time.sleep(0.3); u.keybd_event(0xA5,0x38,0x0003,None)
time.sleep(0.5)
keyboard.unhook(h)
print('captured:', events)
