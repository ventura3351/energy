import os
import uuid
import zipfile
import threading
import shutil
import subprocess
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app, origins=['https://energydownload.netlify.app', 'https://energybackend.ngrok.app'])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Caminho do arquivo de cookies (você deve mantê-lo atualizado)
COOKIES_FILE = os.path.join(BASE_DIR, 'cookies.txt')

jobs = {}

def download_worker(job_id, urls, formato):
    job_folder = os.path.join(DOWNLOAD_FOLDER, job_id)
    os.makedirs(job_folder, exist_ok=True)

    is_single = len(urls) == 1

    jobs[job_id] = {
        'status': 'processing',
        'total': len(urls),
        'completed': 0,
        'logs': [],
        'is_single': is_single,
        'file_path': None,
        'zip_path': None
    }

    # Configurações de formato (igual à desktop)
    formato_opts = {}
    if formato == 'audio_mp3':
        formato_opts['format'] = 'bestaudio/best'
        formato_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        # Verifica ffmpeg
        if not shutil.which('ffmpeg'):
            jobs[job_id]['logs'].append("⚠️ ffmpeg não encontrado. Conversão MP3 pode falhar.")
    elif formato == 'video_720':
        formato_opts['format'] = 'best[height<=720]'
    else:  # video_best
        formato_opts['format'] = 'bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b[ext=mp4]/b'
        formato_opts['merge_output_format'] = 'mp4'

    # Opções de transcrição
    sub_opts = {}
    # (não implementamos transcrição na web por simplicidade)

    # Tentativas (igual à versão desktop)
    tentativas_config = [
        {'name': 'YouTube Web', 'extractor_args': {'youtube': {'player_client': ['web']}}},
        {'name': 'YouTube Mobile/Android', 'extractor_args': {'youtube': {'player_client': ['mweb', 'android']}}},
        {'name': 'YouTube genérico', 'format': 'best[ext=mp4]/best' if formato != 'audio_mp3' else 'ba/b'},
        {'name': 'YouTube Android Embedded', 'extractor_args': {'youtube': {'player_client': ['android_embedded', 'android']}}, 'format': 'best' if formato != 'audio_mp3' else None},
        {'name': 'Modo mínimo', 'format': 'ba/b' if formato == 'audio_mp3' else 'best'},
    ]

    for idx, url in enumerate(urls):
        success = False
        for tentativa in tentativas_config:
            try:
                jobs[job_id]['logs'].append(f"🔄 Tentativa {tentativa['name']} para {url[:60]}...")

                # Montar opções base
                ydl_opts = {
                    'outtmpl': os.path.join(job_folder, '%(title)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                    'ignoreerrors': True,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Referer': url,
                    },
                    'progress_hooks': [lambda d: None],  # simplificado
                }

                # Aplicar opções de formato
                ydl_opts.update(formato_opts)

                # Aplicar opções da tentativa
                if 'extractor_args' in tentativa:
                    ydl_opts['extractor_args'] = tentativa['extractor_args']
                if 'format' in tentativa and tentativa['format']:
                    ydl_opts['format'] = tentativa['format']
                if 'merge_output_format' in formato_opts:
                    ydl_opts['merge_output_format'] = formato_opts['merge_output_format']

                # Usar cookies se disponíveis
                if os.path.exists(COOKIES_FILE):
                    ydl_opts['cookiefile'] = COOKIES_FILE

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info:
                        title = info.get('title', 'video')
                        jobs[job_id]['logs'].append(f"✅ {title} baixado com {tentativa['name']}.")
                        success = True
                        break
            except Exception as e:
                jobs[job_id]['logs'].append(f"❌ Tentativa {tentativa['name']} falhou: {str(e)[:100]}")
                continue

        if success:
            jobs[job_id]['completed'] += 1
        else:
            jobs[job_id]['logs'].append(f"❌ Todas as tentativas falharam para {url[:60]}")

    if is_single:
        files = os.listdir(job_folder)
        if files:
            jobs[job_id]['file_path'] = os.path.join(job_folder, files[0])
            jobs[job_id]['status'] = 'completed_single'
            jobs[job_id]['logs'].append("✅ Download único concluído!")
        else:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['logs'].append("❌ Nenhum arquivo foi baixado.")
    else:
        zip_path = os.path.join(DOWNLOAD_FOLDER, f"{job_id}.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, _, files in os.walk(job_folder):
                for file in files:
                    zipf.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), job_folder))
        jobs[job_id]['zip_path'] = zip_path
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['logs'].append("✅ ZIP gerado com todos os arquivos!")

@app.route('/')
def index():
    cookies_status = "disponível" if os.path.exists(COOKIES_FILE) else "não encontrado"
    ffmpeg_status = "disponível" if shutil.which('ffmpeg') else "não encontrado"
    return f"Energy Downloader API - Cookies: {cookies_status} - FFmpeg: {ffmpeg_status}"

@app.route('/start', methods=['POST', 'OPTIONS'])
def start_download():
    if request.method == 'OPTIONS':
        return '', 200
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
        'logs': job['logs'][-50:],
        'is_single': job.get('is_single', False)
    })

@app.route('/download/<job_id>')
def download(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Job não encontrado", 404
    if job['status'] == 'completed' and job.get('zip_path'):
        if os.path.exists(job['zip_path']):
            return send_file(job['zip_path'], as_attachment=True, download_name=f"energy_{job_id}.zip")
    return "Arquivo não disponível", 404

@app.route('/download_single/<job_id>')
def download_single(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Job não encontrado", 404
    if job['status'] == 'completed_single' and job.get('file_path'):
        if os.path.exists(job['file_path']):
            return send_file(job['file_path'], as_attachment=True)
    return "Arquivo não disponível", 404

@app.route('/cleanup/<job_id>', methods=['POST', 'OPTIONS'])
def cleanup(job_id):
    if request.method == 'OPTIONS':
        return '', 200
    job = jobs.pop(job_id, None)
    if job:
        for path in [job.get('zip_path'), job.get('file_path')]:
            if path and os.path.exists(path):
                os.remove(path)
        job_folder = os.path.join(DOWNLOAD_FOLDER, job_id)
        if os.path.exists(job_folder):
            shutil.rmtree(job_folder)
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)