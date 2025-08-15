# ベースとなるOSとしてPython 3.10のスリム版を選択
FROM python:3.10-slim

# 環境変数: Pythonの出力をバッファリングせず、即座にログに出力させる
ENV PYTHONPATH=/app

# OSをアップデートし、GIF変換に必須のffmpegをインストール
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# アプリケーションを配置するディレクトリを作成
WORKDIR /app

# まずrequirements.txtだけをコピーし、ライブラリをインストールします。
# これにより、アプリのコードだけを変更した場合に、この重い処理がキャッシュされるようになります。
COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションの全ファイルをコピーします。
COPY webapp/ /app/webapp/
COPY core_converter/ /app/core_converter/

# コンテナ起動時にGunicornサーバーを実行 (Renderがポートを自動設定)
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:app"]