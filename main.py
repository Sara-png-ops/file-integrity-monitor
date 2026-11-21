import os
import re
import json
import hashlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
BASELINE_DIR = BASE_DIR / "fim_baselines"

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# GUI state. These are assigned in main(), so importing this module
# (for example from the tests) does not open a window.
selected_folder = None
folder_label = None
status_label = None
results_table = None


# ---------------------------------------------------------------------------
# Core logic (no GUI code in this section, so it can be tested directly)
# ---------------------------------------------------------------------------

def calculate_hash(file_path):
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def scan_folder(folder, excluded_dirs=None):
    """Hash every regular file under `folder`.

    Returns (file_hashes, unreadable):
      file_hashes - {relative_path: sha256_hex} for files that were read
      unreadable  - sorted relative paths that could not be read. Directories
                    that could not be listed are included with a trailing
                    path separator (e.g. "private/").

    Directories in `excluded_dirs` (default: the baseline directory) are
    skipped so the tool never scans its own baseline files.
    """
    folder = Path(folder)
    if excluded_dirs is None:
        excluded_dirs = [BASELINE_DIR]
    excluded = {Path(p).resolve() for p in excluded_dirs}

    file_hashes = {}
    unreadable = []

    def on_walk_error(error):
        # os.walk silently ignores directories it cannot list unless told
        # otherwise. If the folder itself cannot be listed, stop; if a
        # sub-folder cannot be listed, record it as unreadable.
        if Path(error.filename) == folder:
            raise error
        try:
            relative_dir = str(Path(error.filename).relative_to(folder))
        except ValueError:
            relative_dir = str(error.filename)
        unreadable.append(relative_dir + os.sep)

    for dirpath, dirs, files in os.walk(folder, onerror=on_walk_error):
        dirs[:] = [
            d for d in dirs
            if (Path(dirpath) / d).resolve() not in excluded
        ]
        for filename in files:
            file_path = Path(dirpath) / filename
            relative_path = str(file_path.relative_to(folder))
            try:
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                file_hashes[relative_path] = calculate_hash(file_path)
            except OSError:
                unreadable.append(relative_path)

    return file_hashes, sorted(unreadable)


def get_baseline_path(folder):
    folder_id = hashlib.sha256(
        str(Path(folder).resolve()).encode("utf-8")
    ).hexdigest()
    return BASELINE_DIR / f"{folder_id}.json"


def build_baseline(folder, hashes, unreadable):
    return {
        "folder": str(Path(folder).resolve()),
        "created": datetime.now().isoformat(timespec="seconds"),
        "files": hashes,
        "unreadable": unreadable,
    }


def save_baseline(path, data):
    """Write the baseline atomically.

    The data is written to a temporary file first and then moved over the
    real baseline with os.replace(), so a crash mid-write can never leave a
    half-written baseline behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def validate_baseline(baseline, folder):
    """Check the structure of a loaded baseline.

    Returns (files, unreadable). Raises ValueError if anything is wrong.
    Baselines created before the "unreadable" field existed are accepted.
    """
    if not isinstance(baseline, dict):
        raise ValueError("The baseline file is not a valid baseline.")

    if baseline.get("folder") != str(Path(folder).resolve()):
        raise ValueError("The saved baseline does not match this folder.")

    files = baseline.get("files")
    if not isinstance(files, dict):
        raise ValueError("The baseline is missing its list of files.")
    for name, digest in files.items():
        if not isinstance(digest, str) or not SHA256_PATTERN.match(digest):
            raise ValueError(f"The baseline has an invalid hash for '{name}'.")

    unreadable = baseline.get("unreadable", [])
    if not isinstance(unreadable, list) or not all(
        isinstance(item, str) for item in unreadable
    ):
        raise ValueError("The baseline has an invalid unreadable-files list.")

    return files, unreadable


def load_baseline(path, folder):
    with open(path, "r", encoding="utf-8") as file:
        baseline = json.load(file)
    return validate_baseline(baseline, folder)


def _is_unreadable(name, unreadable):
    """True if `name` is listed as unreadable, or lies inside an
    unreadable directory (entries ending with a path separator)."""
    return any(
        name == item or (item.endswith(os.sep) and name.startswith(item))
        for item in unreadable
    )


def compare_snapshots(original, current, unreadable, baseline_unreadable=()):
    """Classify every path as MODIFIED, DELETED, NEW, UNCHANGED or UNREADABLE.

    original            - hashes stored in the baseline
    current             - hashes from the current scan
    unreadable          - paths that could not be read in the current scan
    baseline_unreadable - paths that could not be read when the baseline
                          was created

    A path that cannot be read now is never reported as DELETED or NEW,
    because its real state is unknown.

    Returns (results, counts) where results is a sorted list of
    (name, status, details) tuples.
    """
    results = []
    counts = {
        "MODIFIED": 0,
        "DELETED": 0,
        "NEW": 0,
        "UNCHANGED": 0,
        "UNREADABLE": 0,
    }

    for name in sorted(set(original) | set(current) | set(unreadable)):
        if _is_unreadable(name, unreadable):
            status = "UNREADABLE"
            if name in original:
                details = "Could not be read; integrity cannot be verified"
            else:
                details = "Could not be read; not covered by the baseline"
        elif name not in original:
            status = "NEW"
            if _is_unreadable(name, baseline_unreadable):
                details = "Was unreadable when the baseline was created"
            else:
                details = "New file detected"
        elif name not in current:
            status = "DELETED"
            details = "Previously monitored file is missing"
        elif original[name] != current[name]:
            status = "MODIFIED"
            details = "SHA-256 hash mismatch"
        else:
            status = "UNCHANGED"
            details = "File integrity verified"

        counts[status] += 1
        results.append((name, status, details))

    return results, counts


def format_summary(counts):
    return (
        f"Modified: {counts['MODIFIED']} | "
        f"Deleted: {counts['DELETED']} | "
        f"New: {counts['NEW']} | "
        f"Unreadable: {counts['UNREADABLE']} | "
        f"Unchanged: {counts['UNCHANGED']}"
    )


# ---------------------------------------------------------------------------
# GUI handlers
# ---------------------------------------------------------------------------

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
        hashes, unreadable = scan_folder(selected_folder)
        if not hashes:
            messagebox.showwarning(
                "No files found",
                "No accessible files were found in this folder."
            )
            return

        save_baseline(
            baseline_path,
            build_baseline(selected_folder, hashes, unreadable)
        )

        message = f"Baseline created successfully. {len(hashes)} files recorded."
        if unreadable:
            message += f" {len(unreadable)} could not be read and were not recorded."
        status_label.config(text=message)
        messagebox.showinfo("Baseline created", message)
    except OSError as error:
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
        original, baseline_unreadable = load_baseline(
            baseline_path, selected_folder
        )
        current, unreadable = scan_folder(selected_folder)
        results, counts = compare_snapshots(
            original, current, unreadable, baseline_unreadable
        )

        results_table.delete(*results_table.get_children())
        for name, status, details in results:
            results_table.insert(
                "", "end",
                values=(name, status, details),
                tags=(status,)
            )

        summary = format_summary(counts)
        status_label.config(text=summary)

        if (counts["MODIFIED"] or counts["DELETED"]
                or counts["NEW"] or counts["UNREADABLE"]):
            messagebox.showwarning("Security alert", "Changes detected!\n\n" + summary)
        else:
            messagebox.showinfo(
                "Integrity verified",
                "All monitored files are unchanged.\n\n" + summary
            )

    except (OSError, ValueError) as error:
        messagebox.showerror("Could not check integrity", str(error))


# ---------------------------------------------------------------------------
# GUI construction
# ---------------------------------------------------------------------------

def main():
    global folder_label, status_label, results_table

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
    results_table.tag_configure("UNREADABLE", foreground="#8E24AA")
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


if __name__ == "__main__":
    main()
