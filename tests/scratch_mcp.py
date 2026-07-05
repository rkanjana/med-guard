import subprocess
import sys
import time

p = subprocess.Popen(
    [sys.executable, "-u", "-m", "app.mcp_server"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True
)

time.sleep(2)
ret = p.poll()
if ret is not None:
    print("Process exited with code", ret)
    print("Stdout:", p.stdout.read())
    print("Stderr:", p.stderr.read())
else:
    print("Process is running.")
    p.terminate()
