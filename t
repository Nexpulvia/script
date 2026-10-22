
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
        messagebox.showerror("PyPicture", "Invalid PNG")
        return
    p = M + zlib.compress(Path(b).read_bytes())
    c = struct.pack(">I", len(p)) + b"tEXt" + p
    c += struct.pack(">I", zlib.crc32(b"tEXt" + p) & 0xffffffff)
    i = 8
    while i + 12 <= len(d):
        n = struct.unpack(">I", d[i:i+4])[0]
        k = d[i+4:i+8]
        if k == b"IEND":
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
        messagebox.showerror("PyPicture", str(e))
        return

    w = tk.Toplevel(root)
    w.title("PyPicture")
    w.geometry("360x130")
    tk.Label(w, text=Path(f).name).pack(pady=12)

    def run():
        if messagebox.askyesno("PyPicture", "Run image?"):
            try:
                exec(compile(s, "<image>", "exec"),
                     {"__name__": "__main__", "__file__": "<image>"})
            except Exception as e:
                messagebox.showerror("Error", str(e))

    tk.Button(w, text="Run image", command=run).pack()

root = tk.Tk()
root.title("PyPicture")
root.geometry("240x120")
root.resizable(False, False)
tk.Label(root, text="PyPicture", font=("Arial", 16)).pack(pady=10)
tk.Button(root, text="Embed", width=20, command=embed).pack(pady=3)
tk.Button(root, text="Open", width=20, command=open_image).pack(pady=3)
root.mainloop()
