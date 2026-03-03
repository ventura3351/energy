import os
import uuid
import zipfile
import threading
import shutil
import tempfile
from flask import Flask, request, jsonify, send_file
import yt_dlp

app = Flask(__name__)
# Usar diretório temporário do sistema (no Render é /tmp)
BASE_DIR = tempfile.gettempdir()
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'energy_downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

jobs = {}  # dicionário para armazenar status (simplificado; em produção use Redis)

def download_worker(job_id, urls, formato):
    job_folder = os.path.join(DOWNLOAD_FOLDER, job_id)
    os.makedirs(job_folder, exist_ok=True)

    jobs[job_id] = {
        'status': 'processing',
        'total': len(urls),
        'completed': 0,
        'logs': [],
        'zip_path': None
    }

    ydl_opts = {
        'outtmpl': os.path.join(job_folder, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ignoreerrors': True,
    }

    if formato == 'audio_mp3':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif formato == 'video_720':
        ydl_opts['format'] = 'best[height<=720]'
    else:
        ydl_opts['format'] = 'best'

    for idx, url in enumerate(urls):
        try:
            jobs[job_id]['logs'].append(f"Iniciando {idx+1}/{len(urls)}: {url[:60]}...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            jobs[job_id]['completed'] += 1
            jobs[job_id]['logs'].append(f"✅ Concluído {idx+1}/{len(urls)}")
        except Exception as e:
            jobs[job_id]['logs'].append(f"❌ Erro: {str(e)[:100]}")

    # Criar ZIP
    zip_filename = f"{job_id}.zip"
    zip_path = os.path.join(DOWNLOAD_FOLDER, zip_filename)
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, _, files in os.walk(job_folder):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, job_folder)
                zipf.write(file_path, arcname)

    jobs[job_id]['status'] = 'completed'
    jobs[job_id]['zip_path'] = zip_path
    jobs[job_id]['logs'].append("✅ Todos concluídos! ZIP gerado.")

@app.route('/')
def home():
    return "Energy Downloader API - Use as rotas /start, /status/<id>, /download/<id>, /cleanup/<id>"

@app.route('/start', methods=['POST'])
def start_download():
    data = request.get_json()
    urls_text = data.get('urls', '').strip()
    formato = data.get('formato', 'video_best')

    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    if not urls:
        return jsonify({'error': 'Nenhuma URL fornecida.'}), 400

    job_id = str(uuid.uuid4())
    thread = threading.Thread(target=download_worker, args=(job_id, urls, formato))
    thread.daemon = True
    thread.start()

    return jsonify({'job_id': job_id})

@app.route('/status/<job_id>')
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job não encontrado'}), 404
    return jsonify({
        'status': job['status'],
        'total': job['total'],
        'completed': job['completed'],
        'logs': job['logs'][-50:]
    })

@app.route('/download/<job_id>')
def download(job_id):
    job = jobs.get(job_id)
    if not job or job['status'] != 'completed':
        return "Download não disponível", 404
    zip_path = job.get('zip_path')
    if not zip_path or not os.path.exists(zip_path):
        return "Arquivo não encontrado", 404
    return send_file(zip_path, as_attachment=True, download_name=f"energy_{job_id}.zip")

@app.route('/cleanup/<job_id>', methods=['POST'])
def cleanup(job_id):
    job = jobs.pop(job_id, None)
    if job:
        zip_path = job.get('zip_path')
        job_folder = os.path.join(DOWNLOAD_FOLDER, job_id)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if os.path.exists(job_folder):
                shutil.rmtree(job_folder)
        except Exception:
            pass
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)