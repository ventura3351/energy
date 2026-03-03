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
# Permitir origens do Netlify e ngrok
CORS(app, origins=['https://energydownload.netlify.app', 'https://energybackend.ngrok.app'])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Caminho do arquivo de cookies (coloque o cookies.txt na mesma pasta)
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

    # Configurações base do yt-dlp
    ydl_opts = {
        'outtmpl': os.path.join(job_folder, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ignoreerrors': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
    }

    # Adicionar cookies se o arquivo existir
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        jobs[job_id]['logs'].append("🍪 Cookies carregados com sucesso.")
    else:
        jobs[job_id]['logs'].append("⚠️ Arquivo de cookies não encontrado. Downloads do YouTube podem falhar.")

    # Configurações específicas de formato
    formato_opts = {}
    if formato == 'audio_mp3':
        formato_opts['format'] = 'bestaudio/best'
        formato_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        # Verificar ffmpeg
        if not shutil.which('ffmpeg'):
            jobs[job_id]['logs'].append("⚠️ ffmpeg não encontrado. A conversão para MP3 pode falhar.")
    elif formato == 'video_720':
        formato_opts['format'] = 'best[height<=720]'
    else:  # video_best
        formato_opts['format'] = 'bestvideo+bestaudio/best'

    ydl_opts.update(formato_opts)

    # Função de progresso (simplificada)
    def progress_hook(d):
        if d['status'] == 'downloading':
            # Não vamos enviar progresso detalhado para não sobrecarregar
            pass
        elif d['status'] == 'finished':
            jobs[job_id]['logs'].append(f"✅ Download concluído: {d['filename']}")

    ydl_opts['progress_hooks'] = [progress_hook]

    for idx, url in enumerate(urls):
        try:
            jobs[job_id]['logs'].append(f"Iniciando {idx+1}/{len(urls)}: {url[:60]}...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Tentar extrair info para ver se há erro
                info = ydl.extract_info(url, download=True)
                if info:
                    title = info.get('title', 'video')
                    jobs[job_id]['logs'].append(f"✅ {title} baixado com sucesso.")
                jobs[job_id]['completed'] += 1
        except Exception as e:
            error_msg = str(e)
            jobs[job_id]['logs'].append(f"❌ Erro no download {idx+1}: {error_msg[:150]}")
            # Se for YouTube e erro de formato, tentar fallback simples
            if 'youtube' in url.lower() and 'formato' in error_msg.lower():
                try:
                    jobs[job_id]['logs'].append("🔄 Tentando fallback: melhor qualidade disponível...")
                    ydl_opts_fallback = ydl_opts.copy()
                    ydl_opts_fallback['format'] = 'best'  # formato mais simples
                    with yt_dlp.YoutubeDL(ydl_opts_fallback) as ydl2:
                        ydl2.download([url])
                    jobs[job_id]['completed'] += 1
                    jobs[job_id]['logs'].append("✅ Download concluído com fallback.")
                except Exception as e2:
                    jobs[job_id]['logs'].append(f"❌ Fallback também falhou: {str(e2)[:100]}")
            continue

    if is_single:
        # Encontrar o arquivo baixado (pode ser .mp4, .webm, .mp3)
        files = os.listdir(job_folder)
        if files:
            # Pega o primeiro arquivo (deve ser o único)
            file_path = os.path.join(job_folder, files[0])
            jobs[job_id]['file_path'] = file_path
            jobs[job_id]['status'] = 'completed_single'
            jobs[job_id]['logs'].append("✅ Download único concluído!")
        else:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['logs'].append("❌ Nenhum arquivo foi baixado.")
    else:
        # Criar ZIP
        zip_filename = f"{job_id}.zip"
        zip_path = os.path.join(DOWNLOAD_FOLDER, zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, _, files in os.walk(job_folder):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, job_folder)
                    zipf.write(file_path, arcname)
        jobs[job_id]['zip_path'] = zip_path
        jobs[job_id]['status'] = 'completed'
        jobs[job_id]['logs'].append("✅ ZIP gerado com todos os arquivos!")

@app.route('/')
def index():
    cookies_status = "disponível" if os.path.exists(COOKIES_FILE) else "não encontrado"
    ffmpeg_status = "disponível" if shutil.which('ffmpeg') else "não encontrado (MP3 pode falhar)"
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
        zip_path = job['zip_path']
        if os.path.exists(zip_path):
            return send_file(zip_path, as_attachment=True, download_name=f"energy_{job_id}.zip")
    return "Arquivo não disponível", 404

@app.route('/download_single/<job_id>')
def download_single(job_id):
    job = jobs.get(job_id)
    if not job:
        return "Job não encontrado", 404
    if job['status'] == 'completed_single' and job.get('file_path'):
        file_path = job['file_path']
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True)
    return "Arquivo não disponível", 404

@app.route('/cleanup/<job_id>', methods=['POST', 'OPTIONS'])
def cleanup(job_id):
    if request.method == 'OPTIONS':
        return '', 200
    job = jobs.pop(job_id, None)
    if job:
        zip_path = job.get('zip_path')
        file_path = job.get('file_path')
        job_folder = os.path.join(DOWNLOAD_FOLDER, job_id)
        try:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
            if os.path.exists(job_folder):
                shutil.rmtree(job_folder)
        except Exception:
            pass
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)