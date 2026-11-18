import os
import json
import hashlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
BASELINE_DIR = BASE_DIR / "fim_baselines"
BASELINE_DIR.mkdir(exist_ok=True)
selected_folder = None


def calculate_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def scan_folder(folder):
    file_hashes = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [
            d for d in dirs
            if not (Path(root) / d).is_symlink()
        ]
        for filename in files:
            file_path = Path(root) / filename
            try:
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                relative_path = str(file_path.relative_to(folder))
                file_hashes[relative_path] = calculate_hash(file_path)
            except OSError:
                continue
    return file_hashes


def get_baseline_path(folder):
    folder_id = hashlib.sha256(
        str(folder.resolve()).encode("utf-8")
    ).hexdigest()
    return BASELINE_DIR / f"{folder_id}.json"


def select_folder():
    global selected_folder
    folder = filedialog.askdirectory(title="Select Folder to Monitor")
    if folder:
        selected_folder = Path(folder)
        folder_label.config(text=str(selected_folder))
        status_label.config(text="Folder selected. Create a baseline.")


def create_baseline():
    if selected_folder is None:
        messagebox.showwarning("Select a folder", "Please select a folder first.")
        return

    baseline_path = get_baseline_path(selected_folder)
    if baseline_path.exists():
        if not messagebox.askyesno(
            "Replace baseline?",
            "An existing baseline was found. Do you want to replace it?"
        ):
            return

    try:
        hashes = scan_folder(selected_folder)
        if not hashes:
            messagebox.showwarning(
                "No files found",
                "No accessible files were found in this folder."
            )
            return

        data = {
            "folder": str(selected_folder.resolve()),
            "created": datetime.now().isoformat(timespec="seconds"),
            "files": hashes
        }

        with open(baseline_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)

        status_label.config(
            text=f"Baseline created successfully. {len(hashes)} files recorded."
        )
        messagebox.showinfo(
            "Baseline created",
            f"Successfully recorded {len(hashes)} files."
        )
    except (OSError, PermissionError) as error:
        messagebox.showerror("Could not create baseline", str(error))


def check_integrity():
    if selected_folder is None:
        messagebox.showwarning("Select a folder", "Please select a folder first.")
        return

    baseline_path = get_baseline_path(selected_folder)
    if not baseline_path.exists():
        messagebox.showwarning(
            "No baseline found",
            "Create a baseline before checking file integrity."
        )
        return

    try:
        with open(baseline_path, "r", encoding="utf-8") as file:
            baseline = json.load(file)

        if baseline.get("folder") != str(selected_folder.resolve()):
            raise ValueError("The saved baseline does not match this folder.")

        original = baseline["files"]
        current = scan_folder(selected_folder)

        results_table.delete(*results_table.get_children())

        counts = {"MODIFIED": 0, "DELETED": 0, "NEW": 0, "UNCHANGED": 0}

        for filename in sorted(set(original) | set(current)):
            if filename not in original:
                status, details = "NEW", "New file detected"
            elif filename not in current:
                status, details = "DELETED", "Previously monitored file is missing"
            elif original[filename] != current[filename]:
                status, details = "MODIFIED", "SHA-256 hash mismatch"
            else:
                status, details = "UNCHANGED", "File integrity verified"

            counts[status] += 1
            results_table.insert(
                "", "end",
                values=(filename, status, details),
                tags=(status,)
            )

        summary = (
            f"Modified: {counts['MODIFIED']} | "
            f"Deleted: {counts['DELETED']} | "
            f"New: {counts['NEW']} | "
            f"Unchanged: {counts['UNCHANGED']}"
        )
        status_label.config(text=summary)

        if counts["MODIFIED"] or counts["DELETED"] or counts["NEW"]:
            messagebox.showwarning("Security alert", "Changes detected!\n\n" + summary)
        else:
            messagebox.showinfo(
                "Integrity verified",
                "All monitored files are unchanged.\n\n" + summary
            )

    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        messagebox.showerror("Could not check integrity", str(error))


root = tk.Tk()
root.title("File Integrity Monitoring System")
root.geometry("950x600")
root.minsize(750, 450)
root.configure(bg="#F4F0F8")

tk.Label(
    root,
    text="FILE INTEGRITY MONITORING SYSTEM",
    font=("Arial", 19, "bold"),
    bg="#F4F0F8",
    fg="#4B286D"
).pack(pady=(18, 5))

tk.Label(
    root,
    text="Monitor file changes using SHA-256 cryptographic hashing",
    font=("Arial", 10),
    bg="#F4F0F8",
    fg="#555555"
).pack()

folder_frame = tk.Frame(root, bg="#F4F0F8")
folder_frame.pack(fill="x", padx=20, pady=18)

tk.Button(
    folder_frame,
    text="Select Folder",
    command=select_folder,
    bg="#6D4A91",
    fg="white",
    font=("Arial", 10, "bold"),
    padx=12,
    pady=6
).pack(side="left", padx=5)

folder_label = tk.Label(
    folder_frame,
    text="No folder selected",
    bg="#F4F0F8",
    fg="#333333",
    wraplength=700,
    justify="left"
)
folder_label.pack(side="left", padx=10)

button_frame = tk.Frame(root, bg="#F4F0F8")
button_frame.pack(pady=5)

tk.Button(
    button_frame,
    text="Create Baseline",
    command=create_baseline,
    bg="#4B8B65",
    fg="white",
    width=18,
    font=("Arial", 10, "bold"),
    pady=7
).pack(side="left", padx=8)

tk.Button(
    button_frame,
    text="Check Integrity",
    command=check_integrity,
    bg="#B66A45",
    fg="white",
    width=18,
    font=("Arial", 10, "bold"),
    pady=7
).pack(side="left", padx=8)

status_label = tk.Label(
    root,
    text="Select a folder to begin monitoring.",
    bg="#F4F0F8",
    fg="#333333",
    font=("Arial", 10, "bold"),
    wraplength=850
)
status_label.pack(pady=14)

table_frame = tk.Frame(root)
table_frame.pack(fill="both", expand=True, padx=20, pady=10)

columns = ("File Name", "Status", "Details")
results_table = ttk.Treeview(
    table_frame,
    columns=columns,
    show="headings"
)

results_table.heading("File Name", text="FILE NAME")
results_table.heading("Status", text="STATUS")
results_table.heading("Details", text="DETAILS")
results_table.column("File Name", width=350)
results_table.column("Status", width=130, anchor="center")
results_table.column("Details", width=350)

results_table.tag_configure("MODIFIED", foreground="#C07820")
results_table.tag_configure("DELETED", foreground="#C62828")
results_table.tag_configure("NEW", foreground="#2467A6")
results_table.tag_configure("UNCHANGED", foreground="#27804A")

scrollbar = ttk.Scrollbar(
    table_frame,
    orient="vertical",
    command=results_table.yview
)
results_table.configure(yscrollcommand=scrollbar.set)
results_table.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

tk.Label(
    root,
    text="Cybersecurity Project | SHA-256 File Integrity Verification",
    bg="#F4F0F8",
    fg="#777777"
).pack(pady=10)

root.mainloop()
