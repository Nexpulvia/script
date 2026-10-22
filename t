
import struct
import zlib
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MARKER = b"PyPicture:Python:"

def embed():
    image = filedialog.askopenfilename(
        title="Select PNG",
        filetypes=[("PNG images", "*.png")]
    )
    if not image:
        return

    script = filedialog.askopenfilename(
        title="Select Python script",
        filetypes=[("Python files", "*.py")]
    )
    if not script:
        return

    data = Path(image).read_bytes()
    if not data.startswith(SIGNATURE):
        messagebox.showerror("Error", "Invalid PNG file")
        return

    source = Path(script).read_bytes()
    payload = MARKER + zlib.compress(source)
    chunk = (
        struct.pack(">I", len(payload))
        + b"tEXt"
        + payload
    )
    chunk += struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xffffffff)

    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        pos += 12 + length
        if kind == b"IEND":
            pos -= 12 + length
            break

    output = Path(image).with_name(Path(image).stem + "_embedded.png")
    output.write_bytes(data[:pos] + chunk + data[pos:])
    messagebox.showinfo("PyPicture", f"Created:\n{output}")

def extract(path):
    data = Path(path).read_bytes()
    pos = 8

    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        start = pos + 8
        payload = data[start:start + length]

        if kind == b"tEXt" and payload.startswith(MARKER):
            return zlib.decompress(payload[len(MARKER):])

        pos += length + 12
        if kind == b"IEND":
            break

    raise ValueError("No embedded Python source found")

def viewer():
    path = filedialog.askopenfilename(
        title="Open PyPicture",
        filetypes=[("PNG images", "*.png")]
    )
    if not path:
        return

    try:
        source = extract(path)
    except Exception as e:
        messagebox.showerror("PyPicture", str(e))
        return

    window = tk.Toplevel(root)
    window.title("PyPicture Viewer")
    window.geometry("500x180")

    tk.Label(
        window,
        text=Path(path).name,
        font=("Arial", 12, "bold")
    ).pack(pady=12)

    tk.Label(
        window,
        text="This image contains an embedded Python script."
    ).pack(pady=4)

    def run():
        if not messagebox.askyesno(
            "Confirm execution",
            "Run the embedded Python code?\nOnly continue if you trust its source."
        ):
            return

        try:
            exec(
                compile(source, "<embedded>", "exec"),
                {"__name__": "__main__", "__file__": "<embedded>"}
            )
        except Exception as e:
            messagebox.showerror("Execution error", str(e))

    tk.Button(
        window,
        text="Run embedded Python",
        command=run
    ).pack(pady=12)

root = tk.Tk()
root.title("PyPicture")
root.geometry("320x160")

tk.Label(
    root,
    text="PyPicture",
    font=("Arial", 18, "bold")
).pack(pady=15)

tk.Button(root, text="Embed Python into PNG", command=embed).pack(pady=5)
tk.Button(root, text="Open PyPicture", command=viewer).pack(pady=5)

root.mainloop()
