import struct
import zlib
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

PNG_SIG = b"\x89PNG\r\n\x1a\n"
MARKER = b"PyPicture:Python:"


def iter_chunks(data):
    """Yield (offset, type, payload) for each PNG chunk, stopping at IEND."""
    i = 8
    while i + 12 <= len(data):
        n = struct.unpack(">I", data[i:i+4])[0]
        kind = data[i+4:i+8]
        payload = data[i+8:i+8+n]
        if len(payload) != n:
            return
        yield i, kind, payload
        if kind == b"IEND":
            return
        i += n + 12


def embed():
    png_path = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
    if not png_path:
        return
    script_path = filedialog.askopenfilename(filetypes=[("Python", "*.py")])
    if not script_path:
        return

    data = Path(png_path).read_bytes()
    if not data.startswith(PNG_SIG):
        messagebox.showerror("Error", "Invalid PNG")
        return

    payload = MARKER + zlib.compress(Path(script_path).read_bytes())
    chunk = struct.pack(">I", len(payload)) + b"tEXt" + payload
    chunk += struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xffffffff)

    for offset, kind, _ in iter_chunks(data):
        if kind == b"IEND":
            break

    out = Path(png_path).with_name(Path(png_path).stem + "_embedded.png")
    out.write_bytes(data[:offset] + chunk + data[offset:])
    messagebox.showinfo("PyPicture", f"Saved: {out.name}")


def extract(path):
    data = Path(path).read_bytes()
    if not data.startswith(PNG_SIG):
        raise ValueError("Invalid PNG")
    for _, kind, payload in iter_chunks(data):
        if kind == b"tEXt" and payload.startswith(MARKER):
            return zlib.decompress(payload[len(MARKER):])
    raise ValueError("No embedded script")


def open_image():
    path = filedialog.askopenfilename(filetypes=[("PNG", "*.png")])
    if not path:
        return
    try:
        script = extract(path)
    except Exception as e:
        messagebox.showerror("Error", str(e))
        return
    try:
        exec(compile(script, "<image>", "exec"),
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
