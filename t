
import struct
import zlib
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

S = b"\x89PNG\r\n\x1a\n"
M = b"PyPicture:Python:"

def embed():
    a = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
    if not a:
        return
    b = filedialog.askopenfilename(filetypes=[("Python", "*.py")])
    if not b:
        return
    d = Path(a).read_bytes()
    if not d.startswith(S):
        messagebox.showerror("Error", "Invalid PNG")
        return
    p = M + zlib.compress(Path(b).read_bytes())
    c = struct.pack(">I", len(p)) + b"tEXt" + p
    c += struct.pack(">I", zlib.crc32(b"tEXt" + p) & 0xffffffff)
    i = 8
    while i + 12 <= len(d):
        n = struct.unpack(">I", d[i:i+4])[0]
        if d[i+4:i+8] == b"IEND":
            break
        i += n + 12
    o = Path(a).with_name(Path(a).stem + "_embedded.png")
    o.write_bytes(d[:i] + c + d[i:])
    messagebox.showinfo("PyPicture", f"Saved: {o.name}")

def extract(f):
    d = Path(f).read_bytes()
    if not d.startswith(S):
        raise ValueError("Invalid PNG")
    i = 8
    while i + 12 <= len(d):
        n = struct.unpack(">I", d[i:i+4])[0]
        k = d[i+4:i+8]
        p = d[i+8:i+8+n]
        if len(p) != n:
            break
        if k == b"tEXt" and p.startswith(M):
            return zlib.decompress(p[len(M):])
        i += n + 12
        if k == b"IEND":
            break
    raise ValueError("No embedded script")

def open_image():
    f = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
    if not f:
        return
    try:
        s = extract(f)
    except Exception as e:
        messagebox.showerror("Error", str(e))
        return
    if messagebox.askyesno("PyPicture", "Run image?"):
        try:
            exec(compile(s, "<image>", "exec"),
                 {"__name__": "__main__", "__file__": "<image>"})
        except Exception as e:
            messagebox.showerror("Error", str(e))

root = tk.Tk()
root.title("PyPicture")
root.geometry("220x100")
root.resizable(False, False)
tk.Button(root, text="Embed", width=20, command=embed).pack(pady=(12, 4))
tk.Button(root, text="Open / Run", width=20, command=open_image).pack()
root.mainloop()
