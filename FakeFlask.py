from flask import Flask, render_template, send_from_directory
import os

app = Flask(__name__)

BASE_DIR = os.path.abspath("/opt/render/project/src/DOWNLOADS/")


def get_file_size(file_path):
    size = os.path.getsize(file_path)
    # Convert size to a human-readable format
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TB"


@app.route('/')
def index():
    files = sorted(os.listdir(BASE_DIR), key=lambda x: os.path.getmtime(os.path.join(BASE_DIR, x)), reverse=True)
    files = [f for f in files if not f.startswith('.') and f != 'system.php']
    file_info = [(f, get_file_size(os.path.join(BASE_DIR, f))) for f in files]
    return render_template('index.html', files=file_info)


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(BASE_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
