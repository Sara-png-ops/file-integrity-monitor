# File Integrity Monitoring System

A beginner-friendly Python desktop application that monitors a selected folder using SHA-256 hashes.

## Requirements
- Python 3
- Tkinter (usually included with standard Python installations)

No third-party packages are required.

## Run
Open this folder in VS Code, open the terminal, and run:

```bash
python main.py
```

## How to use
1. Create a separate test folder with a few sample files.
2. Run the application and select that test folder.
3. Click **Create Baseline** to save the original SHA-256 hashes.
4. Change, delete, or add a file in the test folder.
5. Click **Check Integrity** to see whether files are MODIFIED, DELETED, NEW, or UNCHANGED.

The baseline JSON files are stored in the `fim_baselines` folder beside `main.py`.

## Important security note
This is an educational prototype. The baseline is stored locally as JSON and is not protected against tampering. A production implementation should protect or digitally sign baseline data and handle inaccessible files explicitly.
