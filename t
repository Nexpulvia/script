import os
import sys
import platform
from pathlib import Path

M = b"\n#---PYSTART---\n"

B = r"""
  ____        ____  _      _                _       
 |  _ \ _   _|  _ \(_) ___| |_ _ __ _   _| |_ ___ 
 | |_) | | | | |_) | |/ __| __| '__| | | | __/ _ \
 |  __/| |_| |  __/| | (__| |_| |  | |_| | ||  __/
 |_|    \__, |_|   |_|\___|\__|_|   \__,_|\__\___|
        |___/         PyPicture
"""

def d() -> Path:
    h = Path.home()
    for n in ("Downloads", "downloads", "Descargas", "Téléchargements"):
        p = h / n
        if p.is_dir():
            return p
    return h

def p(m: str) -> str:
    return input(m).strip().strip('"').strip("'")

def b(i: str, s: str) -> Path:
    a = Path(i).expanduser().resolve()
    c = Path(s).expanduser().resolve()
    if not a.is_file():
        raise FileNotFoundError(f"Image not found: {a}")
    if not c.is_file():
        raise FileNotFoundError(f"Script not found: {c}")
    x = a.read_bytes()
    y = c.read_bytes()
    o = d() / f"{c.stem}.png"
    with open(o, "wb") as f:
        f.write(x)
        f.write(M)
        f.write(y)
    return o

def r(t: Path) -> Path:
    s = platform.system()
    if s == "Windows":
        q = t.with_suffix(".bat")
        q.write_text(
            "@echo off\n"
            f'python -c "import sys;d=open(r\'{t}\',\'rb\').read();i=d.rfind(b\'\\n#---PYSTART---\\n\');exec(d[i+18:])"\n'
            "pause\n",
            encoding="utf-8",
        )
    else:
        q = t.with_suffix(".command" if s == "Darwin" else ".sh")
        q.write_text(
            "#!/usr/bin/env bash\n"
            f'python3 -c "import sys;d=open(r\'{t}\',\'rb\').read();i=d.rfind(b\'\\n#---PYSTART---\\n\');exec(d[i+18:])"\n'
            "read -p 'Press enter to close...'\n",
            encoding="utf-8",
        )
        os.chmod(q, 0o755)
    return q

def m():
    print(B)
    print("[*] PyPicture - Python Embedder for Images")
    print()
    print("[?] Enter path to image (png/jpg):")
    i = p("    > ")
    print("[?] Enter path to python script (.py):")
    s = p("    > ")
    print()
    print("[+] BUILDING...")
    try:
        o = b(i, s)
    except Exception as e:
        print(f"[!] ERROR: {e}")
        sys.exit(1)
    print(f"[+] Embedded image written: {o}")
    try:
        q = r(o)
        print(f"[+] Runner created:        {q}")
    except Exception as e:
        print(f"[!] Could not create runner: {e}")
        q = None
    print()
    print("[+] DONE")
    print(f"    Image   : {o}")
    if q:
        print(f"    Launcher: {q}")
    print()
    print("[i] The .png opens normally in any image viewer.")
    print("[i] Double-click the launcher to execute the embedded script.")
    print()
    input("Press enter to exit...")

if __name__ == "__main__":
    m()
