"""Run web + one elected collector on a managed service; stop both on failure."""
import os
import signal
import subprocess
import sys
import time

def main():
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('Managed deployment requires DATABASE_URL; refusing temporary local storage.')
    children=[]
    def stop(*args):
        for child in children:
            if child.poll() is None: child.terminate()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,stop)
    port=int(os.environ.get('PORT','8000'))
    try:
        children.append(subprocess.Popen([sys.executable,'-u','server.py','--worker']))
        children.append(subprocess.Popen([sys.executable,'-m','gunicorn','--bind',f'0.0.0.0:{port}',
            '--workers','1','--threads','4','--timeout','60','--error-logfile','-',
            # Query paths with private share tokens are not logged.
            'server:application']))
        while all(child.poll() is None for child in children): time.sleep(0.5)
    finally:
        stop()
        for child in children:
            try: child.wait(timeout=12)
            except subprocess.TimeoutExpired: child.kill();child.wait()
    return 1

if __name__=='__main__': sys.exit(main())
