# --- 1. ベースイメージの選択 ---
# Python 3.10のスリムバージョンをベースにします。
FROM python:3.10-slim

# --- 2. システム依存関係のインストール ---
# FFmpegをインストールし、キャッシュを削除してイメージサイズを小さく保ちます。
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# --- 環境変数の設定 ---
# /app をPythonの検索パスに追加し、core_converterモジュールを確実に見つけられるようにします。
ENV PYTHONPATH=/app

# --- 3. アプリケーションのセットアップ ---
# コンテナ内の作業ディレクトリを設定します。
WORKDIR /app

# まずrequirements.txtだけをコピーしてライブラリをインストールします。
# これにより、コードを変更してもライブラリの再インストールが走らず、ビルドが高速になります。
COPY webapp/requirements.txt ./webapp/requirements.txt
RUN pip install --no-cache-dir -r ./webapp/requirements.txt

# アプリケーションのコード全体をコピーします。
COPY webapp/ ./webapp/
COPY core_converter/ ./core_converter/

# --- 4. アプリケーションの起動 ---
# Gunicornの作業ディレクトリをwebappに設定し、app:appを起動します。
# Renderが提供するPORT環境変数をリッスンします。
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "--chdir", "webapp", "app:app"]