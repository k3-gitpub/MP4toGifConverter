import os
import uuid
import shutil
import sys
from flask import Flask, request, send_file, jsonify, url_for, stream_with_context, Response, render_template
from celery.result import AsyncResult

# 独自ライブラリのインポート
from core_converter import conversion
from webapp.tasks import celery_app, convert_video_to_gif_task
app = Flask(__name__)

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
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'outputs'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

# 起動時にディレクトリが存在することを確認・作成
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
    
@app.route('/convert', methods=['POST'])
def start_conversion_task():
    """MP4ファイルを受け取り、非同期の変換タスクを開始します。"""
    # 1. リクエストのバリデーション
    if 'file' not in request.files:
        return jsonify({"error": "ファイルがリクエストに含まれていません"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "ファイルが選択されていません"}), 400

    # 2. パラメータの取得とデフォルト値の設定
    try:
        start_time = request.form.get('start_time', 0, type=float)
        # end_timeは空の場合があるので、Noneとして扱う
        end_time_str = request.form.get('end_time')
        end_time = float(end_time_str) if end_time_str else None
        fps = request.form.get('fps', 10, type=int)
        width = request.form.get('width', 320, type=int)
        # チェックボックスがONの場合"true"が、OFFの場合Noneが送られてくる
        high_quality = request.form.get('high_quality') == 'true'
    except (ValueError, TypeError):
        return jsonify({"error": "パラメータの型が不正です"}), 400

    # 3. 一時ファイルの準備
    # 安全なファイル名を生成するためにUUIDを使用
    unique_id = str(uuid.uuid4())
    input_filename = f"{unique_id}.mp4"
    output_filename = f"{unique_id}.gif"
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], input_filename)
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    # 4. アップロードされたファイルをサーバーに保存
    file.save(input_path)

    # 5. Celeryタスクを呼び出してバックグラウンド処理を開始 (動画情報の取得もタスク内で行う)
    task = convert_video_to_gif_task.delay(
        input_path, output_path, start_time, end_time, fps, width, high_quality
    )

    # 6. タスクIDとステータス確認用URLをクライアントに即座に返す
    return jsonify({
        "task_id": task.id,
        "status_url": url_for('get_task_status', task_id=task.id)
    }), 202  # 202 Accepted: リクエストは受理されたが、処理は完了していない

@app.route('/status/<task_id>')
def get_task_status(task_id):
    """タスクの現在の状態を返します。"""
    task_result = AsyncResult(task_id, app=celery_app)
    
    response_data = {
        'task_id': task_id,
        'state': task_result.state,
    }

    if task_result.state == 'PROGRESS':
        response_data['progress'] = task_result.info.get('progress', 0)
        response_data['step'] = task_result.info.get('step', '')
    elif task_result.state == 'SUCCESS':
        result_data = task_result.result
        filename = os.path.basename(result_data['output_path'])
        response_data['download_url'] = url_for('download_gif', filename=filename)
    if task_result.state == 'FAILURE':
        # タスクが失敗した場合、エラーメッセージを含める。
        # Celeryタスク内で例外がraiseされると、task_result.infoに例外オブジェクトが格納されるため、str()で変換するのが最も安全。
        response_data['error'] = str(task_result.info)
    
    return jsonify(response_data)

@app.route('/download/<filename>')
def download_gif(filename):
    """生成されたGIFファイルをストリームで送信し、その後削除する。"""
    path = os.path.join(app.config['OUTPUT_FOLDER'], filename)

    if not os.path.exists(path):
        return jsonify({"error": "ファイルが見つからないか、既に削除されています。"}), 404

    def generate():
        try:
            with open(path, 'rb') as f:
                yield from f
        finally:
            # このジェネレータが終了した (ストリームが完了した) 後にファイルを削除
            try:
                print(f"ストリーム完了。出力ファイルを削除: {path}")
                os.remove(path)
            except Exception as e:
                app.logger.error(f"一時ファイルの削除に失敗: {e}")

    # stream_with_contextを使って、リクエストコンテキスト内でストリームを処理
    return Response(stream_with_context(generate()), mimetype='image/gif')

if __name__ == '__main__':
    # 開発用サーバーの起動 (本番環境ではGunicornなどを使用)
    app.run(debug=True, host='0.0.0.0', port=5000)
