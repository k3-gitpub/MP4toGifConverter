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
from app import app, tasks_db

# --- ステップC: 設定ファイルのパスを定義 ---
CONFIG_FILE = 'pywebview_config.json'

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
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

DB_FILE = 'tasks_db.json'

def save_tasks_on_close():
    """ウィンドウが閉じられるときにタスクDBをJSONファイルに保存します。"""
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(tasks_db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving tasks: {e}")

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
            webview.OPEN_DIALOG, directory=initial_dir, file_types=file_types
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

        result = window.create_file_dialog(webview.FOLDER_DIALOG, directory=initial_dir)

        if result:
            selected_path = result[0]
            self.config['last_output_dir'] = selected_path
            save_config(self.config)
            return selected_path
        return None

    def get_last_output_dir(self):
        """最後に使用した保存先フォルダのパスを返す"""
        return self.config.get('last_output_dir', '')

def main():
    # コマンドライン引数に '--debug' が含まれていればデバッグモードを有効にする
    is_debug = '--debug' in sys.argv

    # デスクトップアプリとして実行されていることをFlaskアプリに伝える
    app.config['IS_DESKTOP_APP'] = True
    api = Api()

    # 起動時に以前のタスク履歴を読み込む
    load_tasks_on_startup()

    # ウィンドウのサイズとリサイズの可否を設定します
    webview.create_window(
        'MP4 to GIF Converter',
        app,
        js_api=api,
        width=640,         # ウィンドウの幅
        height=960,        # ウィンドウの高さ
        resizable=True     # リサイズを許可
    )
    # http_server=True は create_window ではなく start に渡します
    webview.start(debug=is_debug, http_server=True)

    # ウィンドウが閉じられた後にタスク履歴を保存する
    save_tasks_on_close()
 
if __name__ == '__main__':
    main()
