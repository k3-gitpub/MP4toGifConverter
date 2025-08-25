import os
import sys
import shutil
import urllib.request
import zipfile

def main():
    """
    Downloads and extracts FFmpeg for Windows.
    """
    # --- Configuration ---
        # Using BtbN's LGPL build to ensure license compliance.
    # The gyan.dev essentials build is also LGPL, but this source explicitly separates LGPL/GPL builds.
    # See: https://github.com/BtbN/FFmpeg-Builds/releases
    FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"

    TARGET_DIR = os.path.join("desktop_app", "bin")
    FFMPEG_EXE_PATH = os.path.join(TARGET_DIR, "ffmpeg.exe")
    FFPROBE_EXE_PATH = os.path.join(TARGET_DIR, "ffprobe.exe")
    DOWNLOAD_ZIP_PATH = "ffmpeg-download.zip"

    # --- Check if FFmpeg already exists ---
    if os.path.exists(FFMPEG_EXE_PATH) and os.path.exists(FFPROBE_EXE_PATH):
        print("FFmpeg already found. Setup is complete.")
        return

    try:
        # --- Download ---
        print(f"Downloading FFmpeg from {FFMPEG_URL}...")
        with urllib.request.urlopen(FFMPEG_URL) as response, open(DOWNLOAD_ZIP_PATH, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

        # --- Extract ---
        print("Extracting FFmpeg binaries...")
        os.makedirs(TARGET_DIR, exist_ok=True)
        with zipfile.ZipFile(DOWNLOAD_ZIP_PATH) as z:
            # Extract only ffmpeg.exe and ffprobe.exe from the archive's 'bin' folder
            for filename in ["ffmpeg.exe", "ffprobe.exe"]:
                # The files are inside a directory like 'ffmpeg-7.0-essentials_build/bin/'
                # We find the full path inside the zip and extract it.
                source_path = next((f.filename for f in z.infolist() if f.filename.endswith(f"bin/{filename}")), None)
                if not source_path:
                    raise FileNotFoundError(f"Could not find '{filename}' in the downloaded archive.")

                with z.open(source_path) as source, open(os.path.join(TARGET_DIR, filename), "wb") as target:
                    shutil.copyfileobj(source, target)
        print(f"Successfully placed ffmpeg.exe and ffprobe.exe in '{TARGET_DIR}'.")
    except Exception as e:
        print(f"Error during FFmpeg setup: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # --- Cleanup ---
        if os.path.exists(DOWNLOAD_ZIP_PATH):
            os.remove(DOWNLOAD_ZIP_PATH)

if __name__ == "__main__":
    main()
