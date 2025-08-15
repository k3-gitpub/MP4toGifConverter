import os
import re
import subprocess     # FFmpegを直接実行するためにインポート

def get_video_duration(ffprobe_path, video_path):
    """ffprobeを使って動画の長さを秒単位で取得する"""
    cmd = [
        ffprobe_path,
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        return float(result.stdout)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"ffprobeの実行に失敗しました: {e}")
        # フォールバックとしてNoneを返す
        return None

def run_conversion(
    ffmpeg_path,
    input_path,
    output_path,
    start_time,
    end_time,
    fps,
    width,
    conversion_duration,
    high_quality,
    progress_callback=None
):
    """
    フレームワークに依存しない汎用的なGIF変換関数。
    進捗を通知するためのコールバックを受け取ることができる。
    """
    palette_path = None
    try:
        base_cmd = [ffmpeg_path, '-ss', str(start_time)]
        if end_time is not None:
            duration = float(end_time) - float(start_time)
            base_cmd.extend(['-t', str(duration)])
        base_cmd.extend(['-i', input_path])

        final_process = None

        if high_quality:
            if progress_callback: progress_callback(0, 'Generating Palette')
            palette_path = os.path.join(os.path.dirname(output_path), f"palette_{os.path.basename(input_path)}.png")
            palette_vf = f"fps={fps},scale={width}:-1:flags=lanczos,palettegen"
            palette_cmd = base_cmd + ['-vf', palette_vf, '-y', palette_path]
            
            palette_process = subprocess.run(palette_cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if palette_process.returncode != 0:
                raise Exception(f"Palette generation failed: {palette_process.stderr}")

            if progress_callback: progress_callback(20, 'Creating GIF')
            gif_vf = f"fps={fps},scale={width}:-1:flags=lanczos [x]; [x][1:v] paletteuse"
            gif_cmd = base_cmd + ['-i', palette_path, '-lavfi', gif_vf, '-y', output_path]
            final_process = subprocess.Popen(gif_cmd, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='replace')
        else:
            if progress_callback: progress_callback(0, 'Creating GIF')
            vf_options = f"fps={fps},scale={width}:-1:flags=lanczos"
            cmd = base_cmd + ['-vf', vf_options, '-y', output_path]
            final_process = subprocess.Popen(cmd, stderr=subprocess.PIPE, universal_newlines=True, encoding='utf-8', errors='replace')

        time_pattern = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
        
        for line in iter(final_process.stderr.readline, ''):
            match = time_pattern.search(line)
            if match:
                hours, minutes, seconds, hundredths = map(int, match.groups())
                current_time = hours * 3600 + minutes * 60 + seconds + hundredths / 100

                if conversion_duration > 0:
                    base_progress = 20 if high_quality else 0
                    progress_percentage = (current_time / conversion_duration) * (100 - base_progress)
                    progress = min(100, int(base_progress + progress_percentage))
                    if progress_callback: progress_callback(progress, 'Creating GIF')

        final_process.wait()

        if final_process.returncode != 0:
            raise Exception(f"FFmpeg failed: {final_process.stderr.read()}")

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise Exception("Output GIF file was not created.")

    finally:
        if os.path.exists(input_path): os.remove(input_path)
        if palette_path and os.path.exists(palette_path): os.remove(palette_path)
