import os
from celery import Celery
from core_converter import conversion

# 環境変数からRedisのURLを取得。なければデフォルト値を使用。
REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')

# Celeryアプリケーションのインスタンスを作成します。
# 'tasks'は現在のモジュール名、brokerとbackendは接続先のRedisサーバーを指定します。
celery_app = Celery(
    'tasks', broker=REDIS_URL, backend=REDIS_URL
)

# Flaskアプリと同様にFFmpegのパスを取得します。
FFMPEG_PATH = os.environ.get("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.environ.get("FFPROBE_PATH", "ffprobe")

@celery_app.task(bind=True)
def convert_video_to_gif_task(self, input_path, output_path, start_time, end_time, fps, width, high_quality=False):
    """
    コア変換ロジックを呼び出すCeleryタスクのラッパー。
    """
    try:
        # タスクの最初に動画情報を取得
        self.update_state(state='PROGRESS', meta={'progress': 0, 'step': '動画情報の取得中...'})
        video_duration = conversion.get_video_duration(FFPROBE_PATH, input_path)
        if video_duration is None:
            raise Exception("動画の情報を取得できませんでした。")

        # 実際に変換する区間の長さを計算
        if end_time is not None:
            actual_end_time = min(end_time, video_duration)
            conversion_duration = actual_end_time - start_time
        else:
            conversion_duration = video_duration - start_time

        if conversion_duration <= 0:
            raise ValueError("変換区間が0秒以下です。開始時間と終了時間を確認してください。")

        # Celeryのタスク状態を更新するためのコールバック関数を定義
        def progress_callback(progress, step):
            self.update_state(state='PROGRESS', meta={'progress': progress, 'step': step})

        # 共通ライブラリの変換関数を呼び出す
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
        return {'status': 'SUCCESS', 'output_path': output_path}
    except Exception as e:
        self.update_state(state='FAILURE', meta={'error': str(e)})
        raise # エラーを再発生させ、CeleryにFAILURE状態を記録させる
    finally:
        # 成功・失敗にかかわらず、一時的な入力ファイルをクリーンアップする
        try:
            if os.path.exists(input_path):
                os.remove(input_path)
        except OSError as e:
            # ログは出しても良いが、ここでは無視
            # celery_app.log.get_default_logger().error(f"一時入力ファイルの削除に失敗: {e}")
            pass