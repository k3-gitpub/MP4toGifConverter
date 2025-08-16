import sys
import os

# --- Robust Path Setup ---
# This ensures that the script can find modules in the project's root directory.
# It handles cases where the script is run directly or in an interactive environment.
try:
    # If run as a script, __file__ is defined. The parent of this file's directory is the project root.
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except NameError:
    # __file__ is not defined (e.g., in a REPL). Assume CWD is the project root and path is already set.
    pass

import webview
import json
from pathlib import Path
import logging
import subprocess
import logging.handlers
from app import app, tasks_db

# --- アプリケーションデータディレクトリの設定 ---
# ユーザーの環境を汚さないよう、設定ファイルは専用のフォルダに保存します。
# クロスプラットフォームで動作するよう、ユーザーのホームディレクトリ以下に作成します。
# 例: C:\Users\YourUser\.mp4togifconverter
APP_NAME = "MP4toGIFConverter"
try:
    # PyInstallerでバンドルされた場合でもホームディレクトリを正しく取得
    APP_DATA_DIR = Path.home() / f".{APP_NAME.lower()}"
    APP_DATA_DIR.mkdir(exist_ok=True) # フォルダがなければ作成
except Exception as e:
    # ホームディレクトリが取得できない稀なケースではカレントディレクトリにフォールバック
    # この時点ではロガーが未設定のため、標準エラー出力にフォールバック
    print(f"CRITICAL: Could not create app data directory in home, falling back to current dir: {e}", file=sys.stderr)
    APP_DATA_DIR = Path('.')

# --- ロギング設定 ---
LOG_FILE = APP_DATA_DIR / 'app.log'

def setup_logging(is_debug=False):
    """アプリケーションのロギングを設定する"""
    log_level = logging.DEBUG if is_debug else logging.INFO

    # ルートロガーを取得し、レベルを設定
    logger = logging.getLogger()
    logger.setLevel(log_level)

    # 既存のハンドラをクリア（重複を避けるため）
    if logger.hasHandlers():
        logger.handlers.clear()

    # ログのフォーマットを定義
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # ファイルハンドラの設定 (ログローテーション付き)
    # 1MBごとにファイルを分け、5世代までバックアップを保持
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"CRITICAL: Could not create log file handler: {e}", file=sys.stderr)

    logging.info("--- Application Starting ---")

def load_config():
    """設定ファイルを読み込む"""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(config):
    """設定ファイルを保存する"""
    try:
        with open(APP_DATA_DIR / 'pywebview_config.json', 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving config: {e}", exc_info=True)

# --- ファイルパスの定義 ---
# 設定ファイルとタスクDBのパスをアプリケーションデータディレクトリ内に設定
CONFIG_FILE = APP_DATA_DIR / 'pywebview_config.json'
DB_FILE = APP_DATA_DIR / 'tasks_db.json'

def save_tasks_on_close():
    """ウィンドウが閉じられた後にタスクDBをJSONファイルに保存します。"""
    logging.info("Application closed. Saving task history.")
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks_db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save task history on close: {e}", exc_info=True)

def load_tasks_on_startup():
    """起動時にJSONファイルからタスクDBを読み込みます。"""
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            tasks_db.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass  # ファイルが存在しない、または空の場合は何もしない

class Api:
    """ pywebviewのJS APIとしてフロントエンドに公開するクラス """
    def __init__(self):
        self.config = load_config()

    def select_file(self):
        """
        ファイル選択ダイアログを開き、選択されたファイルのパスを返す。
        """
        window = webview.active_window()
        if not window:
            return None
        
        initial_dir = self.config.get('last_input_dir') or ''

        file_types = ('MP4 Files (*.mp4)',)
        result = window.create_file_dialog(
            webview.FileDialog.OPEN, directory=initial_dir, file_types=file_types
        )
        
        if result:
            selected_path = result[0]
            self.config['last_input_dir'] = os.path.dirname(selected_path)
            save_config(self.config)
            return selected_path
        return None

    def select_folder(self):
        """
        フォルダ選択ダイアログを開き、選択されたフォルダのパスを返す。
        """
        window = webview.active_window()
        if not window:
            return None
            
        initial_dir = self.config.get('last_output_dir') or ''

        result = window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial_dir)

        if result:
            selected_path = result[0]
            self.config['last_output_dir'] = selected_path
            save_config(self.config)
            return selected_path
        return None

    def get_last_output_dir(self):
        """最後に使用した保存先フォルダのパスを返す"""
        return self.config.get('last_output_dir', '')

    def open_log_folder(self):
        """
        ログファイルが保存されているフォルダをOSのファイルエクスプローラーで開く。
        """
        try:
            folder_path = str(APP_DATA_DIR.resolve())
            if sys.platform == 'win32':
                os.startfile(folder_path)
            elif sys.platform == 'darwin': # macOS
                subprocess.run(['open', folder_path])
            else: # Linux
                subprocess.run(['xdg-open', folder_path])
        except Exception as e:
            logging.error(f"Failed to open log folder '{APP_DATA_DIR}': {e}", exc_info=True)
            # フロントエンドにはエラーを返さない。ログに記録されていれば十分。

def on_closing():
    """
    ウィンドウが閉じられる前に呼び出されるイベントハンドラ。
    変換中のタスクがある場合、ユーザーに終了を確認するダイアログを表示します。
    """
    # 実行中のタスク（完了または失敗していないタスク）があるか確認
    is_task_running = any(
        task.get('state') not in ('SUCCESS', 'FAILURE')
        for task in tasks_db.values()
    )

    if is_task_running:
        window = webview.active_window()
        if window:
            confirm_close = window.create_confirmation_dialog(
                '終了の確認',
                '変換中のタスクがあります。本当にアプリケーションを終了しますか？'
            )
            # ユーザーが「いいえ」(キャンセル) を選択した場合、Falseを返して終了を中止
            if not confirm_close:
                return False
    # 実行中のタスクがない場合、またはユーザーが「はい」を選択した場合は、Trueを返して終了を許可
    return True

def main():
    # コマンドライン引数に '--debug' が含まれていればデバッグモードを有効にする
    is_debug = '--debug' in sys.argv

    # アプリケーションのロギングを設定
    setup_logging(is_debug)

    # デスクトップアプリとして実行されていることをFlaskアプリに伝える
    app.config['IS_DESKTOP_APP'] = True
    api = Api()

    # 起動時に以前のタスク履歴を読み込む
    load_tasks_on_startup()

    # ウィンドウのサイズとリサイズの可否を設定します
    window = webview.create_window(
        'MP4 to GIF Converter',
        app,
        js_api=api,
        width=640,         # ウィンドウの幅
        height=960,        # ウィンドウの高さ
        resizable=True     # リサイズを許可
    )

    # ウィンドウが閉じられる際のイベントにハンドラを接続
    window.events.closing += on_closing
    # ウィンドウが完全に閉じられた後のイベントにハンドラを接続
    window.events.closed += save_tasks_on_close

    # http_server=True は create_window ではなく start に渡します
    webview.start(debug=is_debug, http_server=True)
 
if __name__ == '__main__':
    main()
