import os
import uuid
import shutil
import sys
import json
import threading
import time
from flask import Flask, request, jsonify, url_for, render_template, send_file

# 独自ライブラリのインポート
from core_converter import conversion

# --- WARNING ---
# This implementation uses the filesystem as a simple task store.
# It's a workaround for platforms without free background workers.
app = Flask(__name__)

# --- プロジェクトパス設定 ---
# このファイル(app.py)の場所を基準にプロジェクトのルートディレクトリを特定
# これにより、どこからスクリプトを実行してもパスが安定します
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# --- 設定 ---
# Render.comのような環境では、環境変数からFFmpegのパスを取得するか、
# PATHが通っていることを前提に 'ffmpeg' をコマンド名として使用します。
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")
# --- FFmpegの存在確認 (推奨) ---
# アプリケーション起動時にFFmpegが利用可能かチェックします。
if shutil.which(FFMPEG_PATH) is None:
    print("=" * 60)
    print(f"!!! クリティカルエラー: FFmpegが見つかりません。")
    print(f"    指定されたパス/コマンド: {FFMPEG_PATH}")
    print("    FFmpegをインストールし、PATHを通すか、環境変数 FFMPEG_PATH を設定してください。")
    print("=" * 60)
    sys.exit(1)  # 必須コンポーネントがないため、アプリケーションを終了します。

if shutil.which(FFPROBE_PATH) is None:
    print("=" * 60)
    print(f"!!! クリティカルエラー: ffprobeが見つかりません。")
    print(f"    指定されたパス/コマンド: {FFPROBE_PATH}")
    print("    FFmpegをインストールすると通常は含まれています。PATHを確認してください。")
    print("=" * 60)
    sys.exit(1)  # 必須コンポーネントがないため、アプリケーションを終了します。

# ファイルを一時的に保存するディレクトリ
UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, 'uploads')
OUTPUT_FOLDER = os.path.join(PROJECT_ROOT, 'outputs')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

# 起動時にディレクトリが存在することを確認・作成
# 生成されたファイルを自動削除するまでの秒数 (デフォルト: 1時間)
app.config['CLEANUP_DELAY_SECONDS'] = int(os.environ.get('CLEANUP_DELAY_SECONDS', 3600))

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

@app.route('/')
def index():
    """HTMLページをレンダリングして表示します。"""
    return render_template('index.html')

@app.route('/licenses')
def licenses():
    """ライセンス情報を表示するページ。"""
    return render_template('licenses.html')

def update_task_status(task_id, state, data=None):
    """タスクの状態をJSONファイルに書き込む。"""
    # パス構築と検証をヘルパー関数に一元化する
    status_filepath = get_status_filepath(task_id)
    if not status_filepath:
        print(f"!!! エラー: 無効なtask_id '{task_id}' のため、ステータスを更新できません。")
        return

    status_data = {'state': state}
    if data:
        status_data.update(data)
    with open(status_filepath, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)

def _cleanup_task_files(task_id, paths_to_delete):
    """指定されたタスクに関連するファイル群を安全に削除する。"""
    print(f"タスク {task_id} のクリーンアップを開始します...")
    for path in paths_to_delete:
        try:
            if path and os.path.exists(path):
                os.remove(path)
                print(f"ファイルを削除しました: {path}")
        except OSError as e:
            print(f"ファイル削除中にエラーが発生しました {path}: {e}")

def conversion_worker(task_id, input_path, output_path, start_time, end_time, fps, width, high_quality):
    """バックグラウンドで変換処理を実行し、状態をファイルに記録する関数。"""
    try:
        # 1. 動画の長さを取得
        update_task_status(task_id, 'PROGRESS', {'progress': 0, 'step': '動画情報の取得中...'})
        video_duration = conversion.get_video_duration(FFPROBE_PATH, input_path)
        if video_duration is None:
            raise Exception("動画の情報を取得できませんでした。")

        # 2. 実際に変換する区間の長さを計算
        if end_time is not None:
            actual_end_time = min(end_time, video_duration)
            conversion_duration = actual_end_time - start_time
        else:
            conversion_duration = video_duration - start_time

        if conversion_duration <= 0:
            raise ValueError("変換区間が0秒以下です。開始時間と終了時間を確認してください。")

        # 3. 進捗をファイルに書き込むためのコールバック関数を定義
        def progress_callback(progress, step):
            update_task_status(task_id, 'PROGRESS', {'progress': progress, 'step': step})

        # 4. コア変換処理を呼び出す
        conversion.run_conversion(
            ffmpeg_path=FFMPEG_PATH,
            input_path=input_path,
            output_path=output_path,
            start_time=start_time,
            end_time=end_time,
            fps=fps,
            width=width,
            conversion_duration=conversion_duration,
            high_quality=high_quality,
            progress_callback=progress_callback
        )

        # 5. 変換結果の検証
        # conversion.run_conversionが例外を投げなくても、ffmpegが何らかの理由で
        # ファイル生成に失敗するケースを考慮します。
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise Exception("GIFファイルの生成に失敗しました。ファイルが空か、作成されませんでした。")

        # 6. 成功ステータスを書き込む
        filename = os.path.basename(output_path)
        update_task_status(task_id, 'SUCCESS', {'result': {'output_path': output_path, 'filename': filename}})

    except Exception as e:
        print(f"Task {task_id} failed: {e}")
        update_task_status(task_id, 'FAILURE', {'error': str(e)})
    finally:
        # 変換ワーカーの責務は変換処理のみに限定します。
        # 一時的な入力ファイルは不要になったため、ここで削除します。
        # 出力ファイルとステータスファイルは、後述の定期クリーンアップ処理に任せます。
        try:
            if input_path and os.path.exists(input_path):
                os.remove(input_path)
        except OSError as e:
            print(f"一時入力ファイルの削除に失敗しました {input_path}: {e}")

def get_status_filepath(task_id):
    """ステータスファイルのパスを返すヘルパー関数。"""
    # ファイル名にディレクトリトラバーサルのような危険な文字が含まれていないことを確認
    if not all(c.isalnum() or c in '-_' for c in task_id):
        return None
    return os.path.join(app.config['OUTPUT_FOLDER'], f"{task_id}.status.json")
    
def _parse_conversion_params():
    """フォームから変換パラメータを解析し、辞書またはエラーレスポンスを返す。"""
    try:
        params = {
            'start_time': request.form.get('start_time', 0, type=float),
            'end_time': request.form.get('end_time', default=None, type=float),
            'fps': request.form.get('fps', 10, type=int),
            'width': request.form.get('width', 320, type=int),
            'high_quality': request.form.get('high_quality') == 'true'
        }
        return params, None
    except (ValueError, TypeError):
        return None, (jsonify({"error": "パラメータの型が不正です"}), 400)

@app.route('/convert', methods=['POST'])
def start_conversion_task():
    """MP4ファイルを受け取り、非同期の変換タスクを開始します。"""
    # 1. リクエストのバリデーション
    file = request.files.get('file')

    # ファイルが存在しない、またはファイル名が空の場合はエラー
    if not file or file.filename == '':
        return jsonify({"error": "ファイルが選択されていません"}), 400

    if not file.filename.lower().endswith('.mp4'):
        return jsonify({"error": "MP4形式のファイルのみアップロードできます。"}), 400
    # 2. パラメータの取得とデフォルト値の設定
    params, error_response = _parse_conversion_params()
    if error_response:
        return error_response

    # 3. 一時ファイルの準備
    # 安全なファイル名を生成するためにUUIDを使用
    unique_id = str(uuid.uuid4())
    input_filename = f"{unique_id}.mp4"
    output_filename = f"{unique_id}.gif"
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    # 4. アップロードされたファイルをサーバーに保存
    file.save(input_path)

    # 5. タスクの初期状態をファイルに書き込み、別スレッドで処理を開始
    task_id = unique_id
    update_task_status(task_id, 'PENDING')
    
    # スレッドに渡す引数を一つのタプルにまとめることで、
    # 引数の渡し間違いを防ぎ、コードの可読性を向上させます。
    thread_args = (
        task_id,
        input_path,
        output_path,
        params['start_time'],
        params['end_time'],
        params['fps'],
        params['width'],
        params['high_quality']
    )
    thread = threading.Thread(
        target=conversion_worker,
        args=thread_args
    )
    thread.start()

    # 6. タスクIDとステータス確認用URLをクライアントに返す
    return jsonify({
        "task_id": task_id,
        "status_url": url_for('get_task_status', task_id=task_id)
    }), 202  # 202 Accepted: リクエストは受理されたが、処理は完了していない

@app.route('/status/<task_id>')
def get_task_status(task_id):
    """タスクの現在の状態を返します。"""
    status_filepath = get_status_filepath(task_id)
    if not status_filepath or not os.path.exists(status_filepath):
        return jsonify({'state': 'NOT_FOUND', 'error': 'タスクが見つかりません。'}), 404

    with open(status_filepath, 'r', encoding='utf-8') as f:
        status_data = json.load(f)

    # 成功した場合、ダウンロードURLを追加する
    if status_data.get('state') == 'SUCCESS':
        # conversion_workerで保存したファイル名を直接利用する
        filename = status_data.get('result', {}).get('filename')
        if filename:
            status_data['download_url'] = url_for('download_gif', filename=filename)

    return jsonify(status_data)

@app.route('/download/<filename>')
def download_gif(filename):
    """生成されたGIFファイルを安全に送信する。"""
    # ファイル名に危険な文字が含まれていないか基本的なチェック
    # (UUIDを使っているので通常は安全ですが、念のため)
    if '..' in filename or os.path.isabs(filename):
        return jsonify({"error": "不正なファイル名です。"}), 400

    # send_file を使ってファイルを直接送信します。
    # これにより、ファイルがバイナリモードで正しく扱われることが保証されます。
    file_path = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    try:
        return send_file(
            file_path,
            mimetype='image/gif'
        )
    except FileNotFoundError:
        return jsonify({"error": "ファイルが見つからないか、既に削除されています。"}), 404

def cleanup_scheduler():
    """定期的に古いファイルをクリーンアップするバックグラウンドタスク。"""
    delay = app.config['CLEANUP_DELAY_SECONDS']
    check_interval = 600  # 10分ごとにチェック
    print(f"クリーンアップスケジューラを起動します。{delay}秒より古いファイルを削除します。")
    while True:
        time.sleep(check_interval)
        print("古いファイルのクリーンアップチェックを実行します...")
        now = time.time()
        for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
            try:
                for filename in os.listdir(folder):
                    file_path = os.path.join(folder, filename)
                    if not os.path.isfile(file_path):
                        continue
                    
                    file_mod_time = os.path.getmtime(file_path)
                    if (now - file_mod_time) > delay:
                        os.remove(file_path)
                        print(f"古いファイルを削除しました: {file_path}")
            except Exception as e:
                print(f"クリーンアップ中にエラーが発生しました (フォルダ: {folder}): {e}")

if __name__ == '__main__':
    # デーモンスレッドとしてクリーンアップタスクを開始します。
    # これにより、メインアプリケーションが終了すると、このスレッドも自動的に終了します。
    cleanup_thread = threading.Thread(target=cleanup_scheduler, daemon=True)
    cleanup_thread.start()

    # 開発用サーバーの起動 (本番環境ではGunicornなどを使用)
    # 環境変数からホストとポートを取得し、なければデフォルト値を使用する
    host = os.environ.get('FLASK_RUN_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    # debug=True はリローダーを有効にします。
    # ファイル書き込みによって意図しないリロードが発生し、変換プロセスが中断されるのを防ぐため、
    # use_reloader=False を明示的に指定します。
    app.run(debug=True, host=host, port=port, use_reloader=False)
