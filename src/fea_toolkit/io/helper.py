


def test_func():
    print('Test is working')


def tkinter_file_chooser(verbose=False):
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()

    try:
        # Ask user to select a file
        file_path = filedialog.askopenfilename(
            title="Select SAP2000 JSON file to parse",
            filetypes = [
                ("SAP2000 JSON files", "*.json"), 
                ("SAP2000 files", "*.JSON"), 
                ("Text files", "*.txt"), 
                ("All files", "*.*")
                ]
            )

        root.update()     # Forces macOS to process pending UI events and release the native dialog hook
        root.quit()       # Stops the Tcl/Tk main loop
        root.destroy()    # Safely destroys the root widget

    except tk.TclError:
        if verbose:
            print("❌ TclError - file picking error.")
        file_path = None

    return file_path


def mac_file_chooser(verbose=False):
    import sys
    file_path = None
    # --- MACOS DOCK SUPPRESSION ---
    if sys.platform == "darwin":
        import subprocess
        # Call the native AppleScript file chooser
        cmd = """osascript -e 'POSIX path of (choose file with prompt "Select SAP2000 JSON file to parse" of type {"json", "JSON", "txt"})'"""
        try:
            # Captures the selected file path string
            file_path = subprocess.check_output(cmd, shell=True).decode('utf-8').strip()
        except subprocess.CalledProcessError:
            if verbose:
                print("❌ Selection cancelled by user.")
            file_path = None # User pressed "Cancel"
    return file_path

