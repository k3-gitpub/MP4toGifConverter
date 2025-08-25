import os
import uuid
import shutil
import sys
import subprocess
import logging
import threading
from flask import Flask, request, jsonify, url_for, Response, render_template, stream_with_context, send_file
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

# Flaskアプリのインスタンス化。
# パスに関する設定は、エントリーポイントであるmain.pyに責任を移譲します。
app = Flask(__name__)
# FlaskとWerkzeugのデフォルトロガーを無効にし、main.pyで設定されたロガーに統一する
app.logger.disabled = True 
log = logging.getLogger('werkzeug') 
log.disabled = True 
app.config['IS_DESKTOP_APP'] = False # デフォルトはWebアプリモード

# --- アプリケーションデータフォルダの設定 ---
# Program Filesなどの書き込み禁止領域にインストールされた場合でも
# アプリが正常に動作できるよう、ユーザーのAppDataフォルダにデータ（一時ファイルなど）を保存します。
APP_NAME = "MP4-to-GIF-Converter"
# C:\Users\<ユーザー名>\AppData\Local
base_data_dir = os.getenv('LOCALAPPDATA')
if not base_data_dir:
    # LOCALAPPDATAが取得できない場合のフォールバック
    base_data_dir = os.path.expanduser('~')
app_data_dir = os.path.join(base_data_dir, APP_NAME)

UPLOAD_FOLDER = os.path.join(app_data_dir, 'uploads')
OUTPUT_FOLDER = os.path.join(app_data_dir, 'outputs')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# タスクの状態を保存するためのインメモリ辞書 (ローカル版の簡易DB)
tasks_db = {}

# tasks_dbへのアクセスをスレッドセーフにするためのロック
tasks_db_lock = threading.Lock()

# このモジュール用のロガーインスタンスを取得
logger = logging.getLogger(__name__)

def sanitize_filename(filename: str) -> str:
    """
    ファイル名からパス区切り文字などの危険な文字を削除する。
    werkzeugのsecure_filenameと違い、非ASCII文字は保持する。
    """
    # パス区切り文字を削除
    sanitized = filename.replace('/', '').replace('\\', '')
    # Windowsでファイル名として使えない文字をいくつか削除
    for char in '<>:"|?*':
        sanitized = sanitized.replace(char, '')
    # 先頭や末尾の空白、ドットを削除
    sanitized = sanitized.strip(' .')
    # ファイル名が空になった場合のフォールバック
    if not sanitized:
        return f"converted_{uuid.uuid4().hex[:8]}"
    return sanitized

def get_video_duration(ffprobe_path: str = None, video_path: str = None) -> float | None:
    """ffprobeを使って動画の長さを秒単位で取得する。"""
    # ffprobe_pathが未指定ならFlaskアプリのconfigから取得
    if ffprobe_path is None:
        from flask import current_app
        ffprobe_path = current_app.config.get('FFPROBE_PATH')
    command = [
        ffprobe_path, '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', video_path
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
        logger.error(f"Failed to get video duration for '{video_path}'. Error: {e}", exc_info=True)
        if isinstance(e, subprocess.CalledProcessError):
            logger.error(f"ffprobe stderr: {e.stderr}")
        return None

@dataclass
class ConversionJob:
    """変換ジョブのパラメータを保持するデータクラス"""
    ffmpeg_path: str
    input_path: str
    output_path: str
    start_time: float
    end_time: float | None
    fps: int
    width: int
    conversion_duration: float
    high_quality: bool

def conversion_worker_thread(task_id: str, job: ConversionJob):
    """
    変換処理をバックグラウンドのスレッドで実行し、辞書の状態を更新する。
    """
    # --- FFmpegコマンドの組み立て ---
    # 外部ライブラリに依存せず、直接コマンドを生成することでエラーハンドリングを堅牢にします。
    command = [
        job.ffmpeg_path,
        '-y',  # 出力ファイルを常に上書き
        '-ss', str(job.start_time), # 開始時間
        '-i', job.input_path,      # 入力ファイル
    ]
    if job.end_time is not None:
        command.extend(['-to', str(job.end_time)]) # 終了時間

    # ビデオフィルターの設定
    filters = [
        f"fps={job.fps}",
        f"scale={job.width}:-1:flags=lanczos",
    ]
    if job.high_quality:
        # 高品質モード用のフィルター（2パス処理）
        filters.append("split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
    
    command.extend(['-vf', ','.join(filters)])
    command.append(job.output_path) # 出力ファイル

    try:
        # FFmpegプロセスを直接実行し、エラーがあれば例外を発生させる (check=True)
        # これにより、FFmpegからのエラーメッセージを確実に捕捉できます。
        subprocess.run(
            command,
            check=True,
            capture_output=True, # stdoutとstderrをキャプチャして例外オブジェクトに含める
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0 # Windowsでコンソール非表示
        )
        # 成功したら状態を更新
        with tasks_db_lock:
            tasks_db[task_id]['state'] = 'SUCCESS'
            tasks_db[task_id]['output_path'] = job.output_path
            logger.info(f"Task {task_id} completed successfully. Output: {job.output_path}")
    except subprocess.CalledProcessError as e:
        # FFmpegが0以外の終了コードを返して失敗した場合の特別な処理
        error_message_for_ui = "変換エラー (FFmpeg)。詳細はログファイルを確認してください。"

        # FFmpegの標準エラー出力をデコードしてログに記録する
        ffmpeg_error_output = e.stderr.decode('utf-8', errors='ignore') if e.stderr else "N/A"

        logger.error(
            f"Conversion failed for task {task_id} due to FFmpeg error (exit code {e.returncode}).\n"
            f"FFmpeg Command: {' '.join(map(str, e.cmd))}\n"
            f"FFmpeg stderr:\n{ffmpeg_error_output}"
        )
        with tasks_db_lock:
            tasks_db[task_id]['state'] = 'FAILURE'
            tasks_db[task_id]['error'] = error_message_for_ui
    except Exception as e:
        # FFmpeg以外の予期せぬエラーが発生した場合
        # UIにはエラーの種別がわかる程度のメッセージを表示
        error_message_for_ui = f"変換エラー ({type(e).__name__})。詳細はログファイルを確認してください。"

        # ログにはスタックトレースを含めた詳細な情報を記録
        logger.error(
            f"Conversion failed for task {task_id}. Input: {job.input_path}",
            exc_info=True  # この引数がスタックトレースをログに追加する
        )
        with tasks_db_lock:
            tasks_db[task_id]['state'] = 'FAILURE'
            tasks_db[task_id]['error'] = error_message_for_ui
    finally:
        # 変換が成功しても失敗しても、入力ファイルを削除する
        try:
            if os.path.exists(job.input_path):
                os.remove(job.input_path)
        except OSError as e:
            logger.warning(f"Could not clean up input file {job.input_path}: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/licenses')
def licenses():
    return render_template('licenses.html')

@app.route('/favicon.ico')
def favicon():
    # ブラウザが自動的にリクエストするfavicon.icoに対して、
    # 「コンテンツなし」を返すことで404エラーを防ぎます。
    return '', 204

@app.route('/load-video')
def load_video():
    """
    指定されたパスの動画ファイルを読み込み、プレビュー用にストリーミング配信する。
    セキュリティ: このエンドポイントはユーザーがダイアログで明示的に選択した
    ファイルパスを扱うことを想定しています。任意のパスを読み込めるため、
    Webサーバーとして公開する際には注意が必要です。
    """
    video_path = request.args.get('path')
    if not video_path or not os.path.exists(video_path):
        return jsonify({"error": "File not found"}), 404
    
    if not video_path.lower().endswith('.mp4'):
        return jsonify({"error": "Invalid file type. Only MP4 is supported."}), 400

    try:
        return send_file(video_path, mimetype='video/mp4')
    except Exception as e:
        logger.error(f"Error sending file {video_path}: {e}", exc_info=True)
        return jsonify({"error": "Could not send file. See logs for details."}), 500

@app.route('/open-folder', methods=['POST'])
def open_folder_route():
    if not app.config.get('IS_DESKTOP_APP'):
        return jsonify({"error": "This feature is only available in the desktop app."}), 403
    
    path = request.json.get('path')
    if not path or not os.path.exists(path):
        return jsonify({"error": "Invalid path"}), 400
    
    folder_path = os.path.dirname(path)
    try:
        if sys.platform == 'win32':
            os.startfile(folder_path)
        elif sys.platform == 'darwin': # macOS
            subprocess.run(['open', folder_path])
        else: # Linux
            subprocess.run(['xdg-open', folder_path])
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Failed to open folder '{folder_path}': {e}", exc_info=True)
        return jsonify({"error": "Could not open folder. See logs for details."}), 500

@app.route('/convert', methods=['POST'])
def start_conversion_task():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON request"}), 400

    original_input_path = data.get('input_path')
    if not original_input_path or not os.path.exists(original_input_path):
        return jsonify({"error": "Input file not found or path not provided"}), 400

    original_input_p = Path(original_input_path)

    try:
        start_time = float(data.get('start_time', 0.0))
        end_time_str = data.get('end_time')
        end_time = float(end_time_str) if end_time_str and end_time_str.strip() else None
        fps = int(data.get('fps', 15))
        width = int(data.get('width', 640))
        high_quality = data.get('high_quality', False)
        output_filename_from_form = data.get('output_filename')
        output_dir_from_form = data.get('output_dir')
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid parameter type"}), 400

    task_id = str(uuid.uuid4())
    
    # 元のファイルを直接使わず、安全な場所にコピーして処理する (pathlibを使用)
    upload_dir = Path(app.config['UPLOAD_FOLDER'])
    try:
        input_path_p = upload_dir / f"{task_id}{original_input_p.suffix}"
        shutil.copy(original_input_p, input_path_p)
    except (IOError, OSError) as e:
        # ファイルコピーの失敗は致命的なので、ここでエラーを返す
        logger.error(f"Failed to copy input file from '{original_input_path}' to '{input_path_p}': {e}", exc_info=True)
        return jsonify({"error": f"Could not process input file: {e}"}), 500

    input_path = str(input_path_p) # ワーカースレッドに渡すため文字列に変換

    # 保存先フォルダを決定 (pathlibを使用)
    save_dir_str = output_dir_from_form if output_dir_from_form and output_dir_from_form.strip() else app.config['OUTPUT_FOLDER']
    save_dir_p = Path(save_dir_str)
    save_dir_p.mkdir(parents=True, exist_ok=True)

    # 保存ファイル名を決定 (pathlibを使用)
    if output_filename_from_form and output_filename_from_form.strip():
        final_filename = sanitize_filename(output_filename_from_form)
    elif original_input_p.name:
        # .stemで拡張子なしのファイル名を取得
        final_filename = sanitize_filename(f"{original_input_p.stem}.gif")
    else:
        final_filename = f"{task_id}.gif"
    
    output_path_p = save_dir_p / final_filename
    output_path = str(output_path_p) # ワーカースレッドに渡すため文字列に変換

    # main.pyで設定されたFFmpeg/FFprobeのパスをFlaskのconfigから取得
    ffmpeg_path = app.config.get('FFMPEG_PATH')
    ffprobe_path = app.config.get('FFPROBE_PATH')
    if not ffmpeg_path or not ffprobe_path:
        logger.critical("FFMPEG_PATH or FFPROBE_PATH is not configured in the application.")
        return jsonify({"error": "Server configuration error: FFmpeg path not set."}), 500

    # 進捗計算のために動画の長さを取得
    video_duration = get_video_duration(ffprobe_path, input_path)
    if video_duration is None:
        os.remove(input_path)
        return jsonify({"error": "Could not get video information."}), 500

    if end_time is not None:
        actual_end_time = min(end_time, video_duration)
        conversion_duration = actual_end_time - start_time
    else:
        conversion_duration = video_duration - start_time

    if conversion_duration <= 0:
        os.remove(input_path)
        return jsonify({"error": "Conversion duration must be positive."}), 400

    # タスクの初期状態を辞書に保存
    with tasks_db_lock:
        tasks_db[task_id] = {'state': 'PENDING', 'output_path': output_path}

    # 変換ジョブのパラメータをデータクラスにまとめる
    job = ConversionJob(
        ffmpeg_path=ffmpeg_path,
        input_path=input_path,
        output_path=output_path,
        start_time=start_time,
        end_time=end_time,
        fps=fps,
        width=width,
        conversion_duration=conversion_duration,
        high_quality=high_quality,
    )

    # バックグラウンドスレッドで変換を開始
    thread = threading.Thread(target=conversion_worker_thread, args=(task_id, job))
    thread.daemon = True
    thread.start()

    return jsonify({"task_id": task_id, "status_url": url_for('get_task_status', task_id=task_id)}), 202

@app.route('/status/<task_id>')
def get_task_status(task_id):
    with tasks_db_lock:
        task_info = tasks_db.get(task_id)
        if not task_info:
            return jsonify({'state': 'NOT_FOUND'}), 404
        # ロック内で辞書のコピーを作成し、ロックの外で安全に操作できるようにします
        response_data = task_info.copy()

    response_data['is_desktop_app'] = app.config.get('IS_DESKTOP_APP', False)

    # Webアプリモードの場合のみダウンロードURLを生成
    if not response_data['is_desktop_app'] and response_data.get('state') == 'SUCCESS':
        filename = os.path.basename(response_data['output_path'])
        response_data['download_url'] = url_for('download_gif', filename=filename)
    return jsonify(response_data)
@app.route('/download/<filename>')
def download_gif(filename):
    path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if not os.path.exists(path):
        return jsonify({"error": "File not found or already deleted."}), 404
    
    def generate():
        """ファイルをストリーミングし、完了後に削除するジェネレータ"""
        try:
            with open(path, 'rb') as f:
                yield from f
        finally:
            # デスクトップアプリモードでなければファイルを削除
            if not app.config.get('IS_DESKTOP_APP'):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except OSError as e:
                    logger.error(f"Failed to delete temporary file: {e}", exc_info=True)

    # ストリーミングでレスポンスを返し、ダウンロードダイアログをトリガーする
    return Response(stream_with_context(generate()), mimetype='image/gif', headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })
