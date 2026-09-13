import os, sys, time, frida
root = r'C:\Users\Ricky\Documents\Codex\2026-09-13\wo\work\doubao-probe'
exe = os.path.join(root,'runtime','v0.9.0.0','ImeService.exe')
wav = os.path.join(root,'audio','full_16k_mono.wav')
src = open(os.path.join(root,'scripts','diag_guard.js'), encoding='utf-8').read()
pid = frida.spawn(exe, argv=[exe,'--test-sami','--wav',wav], cwd=os.path.dirname(exe), stdio='inherit')
print('[driver] pid', pid, flush=True)
s = frida.attach(pid); sc = s.create_script(src)
def on_msg(m, d):
    p = m.get('payload') or {}
    if m.get('type')=='send':
        k = p.get('kind')
        if k=='stdout':
            print(p.get('text','').rstrip(), flush=True)
        elif k in ('guard-hit','patched','cmdline','error'):
            print(f'[frida:{k}]', p, flush=True)
    else:
        print('[frida:msg]', m, flush=True)
sc.on('message', on_msg); sc.load(); frida.resume(pid)
print('[driver] resumed', flush=True)
for i in range(75):
    time.sleep(1)
    conns = os.popen(f'powershell -NoProfile -Command "Get-NetTCPConnection -OwningProcess {pid} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty RemoteAddress,RemotePort"').read().strip()
    if i % 5 == 0 and conns:
        print(f'[net {i}s]', conns.replace(chr(10),' '), flush=True)
    if str(pid) not in os.popen(f'tasklist /FI "PID eq {pid}" /NH').read():
        print(f'[driver] exited after ~{i}s', flush=True); break
else:
    print('[driver] timeout, killing', flush=True); frida.kill(pid)
