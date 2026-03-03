import os
import uuid
import zipfile
import threading
import shutil
import subprocess
import logging
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp

# Configuração de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=['https://energydownload.netlify.app', 'https://energybackend.ngrok.app'])

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Caminho do arquivo de cookies (pode ser definido via variável de ambiente)
COOKIES_FILE = os.environ.get('COOKIES_FILE', os.path.join(BASE_DIR, 'cookies.txt'))
if not os.path.exists(COOKIES_FILE):
    logger.warning(f"Arquivo de cookies não encontrado em {COOKIES_FILE}. Downloads podem falhar para vídeos restritos.")
else:
    logger.info(f"Arquivo de cookies carregado: {COOKIES_FILE}")

jobs = {}

def check_ffmpeg():
    """Verifica se ffmpeg está instalado."""
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        return True
    except:
        return False

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

    def log_message(msg):
        logger.info(f"[{job_id}] {msg}")
        jobs[job_id]['logs'].append(msg)

    log_message(f"Iniciando processamento de {len(urls)} URL(s) com formato '{formato}'")

    # Configurações base do yt-dlp
    ydl_opts = {
        'outtmpl': os.path.join(job_folder, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'ignoreerrors': True,
        'logger': logger,
    }

    # Adicionar cookies se existirem
    if os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE
        log_message("Cookies carregados para autenticação.")
    else:
        log_message("Arquivo de cookies não encontrado. Downloads podem falhar.")

    # Verificar ffmpeg para formato de áudio
    if formato == 'audio_mp3' and not check_ffmpeg():
        log_message("FFmpeg não encontrado. A conversão para MP3 pode falhar.")

    for idx, url in enumerate(urls):
        log_message(f"Processando {idx+1}/{len(urls)}: {url[:60]}...")

        # Determinar as opções de formato com fallbacks
        # Primeiro, tenta o formato solicitado. Se falhar, tenta 'best' ou 'bestaudio'
        format_spec = None
        postprocessors = []

        if formato == 'audio_mp3':
            format_spec = 'bestaudio/best'
            postprocessors = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif formato == 'video_720':
            format_spec = 'best[height<=720]'
        else:  # video_best
            format_spec = 'best'

        # Tentativas com fallback
        attempts = [
            {'format': format_spec, 'postprocessors': postprocessors},
            {'format': 'best', 'postprocessors': []},  # fallback 1
            {'format': 'bestaudio', 'postprocessors': []}  # fallback 2 (caso o vídeo não tenha vídeo)
        ]

        success = False
        last_error = None

        for attempt in attempts:
            try:
                opts = ydl_opts.copy()
                opts['format'] = attempt['format']
                if attempt['postprocessors']:
                    opts['postprocessors'] = attempt['postprocessors']

                log_message(f"Tentando formato: {attempt['format']}")
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                success = True
                break
            except Exception as e:
                last_error = str(e)
                log_message(f"Falha com formato {attempt['format']}: {last_error[:100]}")
                continue

        if success:
            jobs[job_id]['completed'] += 1
            log_message(f"✅ Download concluído para {idx+1}/{len(urls)}")
        else:
            log_message(f"❌ Todas as tentativas falharam para esta URL. Último erro: {last_error[:200]}")

    # Após processar todas as URLs, verificar arquivos baixados
    files_downloaded = os.listdir(job_folder)
    log_message(f"Arquivos na pasta: {files_downloaded}")

    if is_single:
        if files_downloaded:
            file_path = os.path.join(job_folder, files_downloaded[0])
            jobs[job_id]['file_path'] = file_path
            jobs[job_id]['status'] = 'completed_single'
            log_message("✅ Download único concluído. Arquivo pronto para download.")
        else:
            jobs[job_id]['status'] = 'error'
            log_message("❌ Nenhum arquivo foi baixado para o link único.")
    else:
        if files_downloaded:
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
            log_message(f"✅ ZIP criado com {len(files_downloaded)} arquivo(s).")
        else:
            jobs[job_id]['status'] = 'error'
            log_message("❌ Nenhum arquivo foi baixado. ZIP não gerado.")

@app.route('/')
def index():
    ffmpeg_status = "disponível" if check_ffmpeg() else "não disponível (MP3 pode falhar)"
    cookies_status = "presente" if os.path.exists(COOKIES_FILE) else "ausente"
    return f"Energy Downloader API - FFmpeg: {ffmpeg_status} - Cookies: {cookies_status}"

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
        except Exception as e:
            logger.error(f"Erro ao limpar job {job_id}: {e}")
    return jsonify({'ok': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)