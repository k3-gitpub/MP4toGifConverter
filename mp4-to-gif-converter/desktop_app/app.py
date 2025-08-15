import os
import uuid
import shutil
import sys
import subprocess
import threading
from flask import Flask, request, jsonify, url_for, Response, render_template, stream_with_context, send_file
from dataclasses import dataclass
from werkzeug.utils import secure_filename
# 共通のコアライブラリをインポート
from core_converter import conversion

def get_resource_path(relative_path):
    """
    リソースへの絶対パスを取得します。開発環境とPyInstallerバンドルの両方で機能します。
    PyInstallerでバンドルされた場合、実行時に作成される一時フォルダ (_MEIPASS) 内の
    パスを返します。
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstallerは一時フォルダを作成し、そのパスを_MEIPASSに格納します
        base_path = sys._MEIPASS
    else:
        try:
            # バンドルされていない、通常のPython環境での実行
            # このファイルの親ディレクトリ（desktop_app）を基準にします
            base_path = os.path.dirname(os.path.abspath(__file__))
        except NameError:
            # __file__ が未定義の場合 (例: REPL)、CWDを基準にします。
            # この場合、ターミナルがプロジェクトのルートディレクトリで
            # 開かれている必要があります。
            base_path = os.path.abspath("desktop_app")
    return os.path.join(base_path, relative_path)

# Flaskアプリのインスタンス化。テンプレートと静的ファイルのパスを明示的に指定します。
template_folder = get_resource_path('templates')
static_folder = get_resource_path('static')
app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
app.config['IS_DESKTOP_APP'] = False # デフォルトはWebアプリモード

# --- 設定 ---
# PyInstallerでバンドルされているかどうかでFFmpeg/FFprobeのパスを切り替える
if getattr(sys, 'frozen', False):
    ffmpeg_binary = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    ffprobe_binary = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    FFMPEG_PATH = get_resource_path(f"bin/{ffmpeg_binary}")
    FFPROBE_PATH = get_resource_path(f"bin/{ffprobe_binary}")
else:
    FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
    FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")

# 実行ファイルの存在チェック
if (not os.path.exists(FFMPEG_PATH) and shutil.which(FFMPEG_PATH) is None) or \
   (not os.path.exists(FFPROBE_PATH) and shutil.which(FFPROBE_PATH) is None):
    print("CRITICAL ERROR: FFmpeg or FFprobe not found.", file=sys.stderr)
    sys.exit(1)

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# タスクの状態を保存するためのインメモリ辞書 (ローカル版の簡易DB)
tasks_db = {}

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
    input_path = job.input_path
    
    # 進捗を辞書に書き込むためのコールバック関数を定義
    def progress_callback(progress, step):
        if task_id in tasks_db:
            tasks_db[task_id].update({'progress': progress, 'step': step})

    try:
        # 共通ライブラリの変換関数を呼び出す
        conversion.run_conversion(
            job.ffmpeg_path,
            job.input_path,
            job.output_path,
            job.start_time,
            job.end_time,
            job.fps,
            job.width,
            job.conversion_duration,
            job.high_quality,
            progress_callback=progress_callback
        )
        # 成功したら状態を更新
        tasks_db[task_id]['state'] = 'SUCCESS'
        tasks_db[task_id]['output_path'] = job.output_path
    except Exception as e:
        # 失敗したらエラーメッセージを保存
        tasks_db[task_id]['state'] = 'FAILURE'
        tasks_db[task_id]['error'] = str(e)
    finally:
        # 変換が成功しても失敗しても、入力ファイルを削除する
        try:
            if os.path.exists(input_path):
                os.remove(input_path)
        except OSError as e:
            # ログ出力が望ましいが、ここでは標準エラーに出力
            print(f"Error cleaning up input file {input_path}: {e}", file=sys.stderr)

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
        app.logger.error(f"Error sending file {video_path}: {e}")
        return jsonify({"error": "Could not send file"}), 500

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
        return jsonify({"error": str(e)}), 500

@app.route('/convert', methods=['POST'])
def start_conversion_task():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON request"}), 400

    original_input_path = data.get('input_path')
    if not original_input_path or not os.path.exists(original_input_path):
        return jsonify({"error": "Input file not found or path not provided"}), 400

    original_filename = os.path.basename(original_input_path)

    try:
        start_time = float(data.get('start_time', 0))
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
    
    # 元のファイルを直接使わず、安全な場所にコピーして処理する
    _, extension = os.path.splitext(original_filename)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{task_id}{extension}")
    shutil.copy(original_input_path, input_path)

    # 保存先フォルダを決定
    save_dir = output_dir_from_form if output_dir_from_form and output_dir_from_form.strip() else app.config['OUTPUT_FOLDER']
    os.makedirs(save_dir, exist_ok=True)

    # 保存ファイル名を決定
    if output_filename_from_form and output_filename_from_form.strip():
        final_filename = secure_filename(output_filename_from_form)
    elif original_filename:
        base, _ = os.path.splitext(original_filename)
        final_filename = secure_filename(f"{base}.gif")
    else:
        final_filename = f"{task_id}.gif"
    output_path = os.path.join(save_dir, final_filename)

    # 進捗計算のために動画の長さを取得
    video_duration = conversion.get_video_duration(FFPROBE_PATH, input_path)
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
    tasks_db[task_id] = {'state': 'PENDING', 'output_path': output_path}

    # 変換ジョブのパラメータをデータクラスにまとめる
    job = ConversionJob(
        ffmpeg_path=FFMPEG_PATH,
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
    task_info = tasks_db.get(task_id)
    if not task_info:
        return jsonify({'state': 'NOT_FOUND'}), 404
    
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
                    app.logger.error(f"Failed to delete temporary file: {e}")

    # ストリーミングでレスポンスを返し、ダウンロードダイアログをトリガーする
    return Response(stream_with_context(generate()), mimetype='image/gif', headers={
        "Content-Disposition": f"attachment; filename={filename}"
    })
