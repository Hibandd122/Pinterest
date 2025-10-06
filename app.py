import time
import random
import re
import requests
from flask import Flask, request, render_template_string, send_file, session
from io import BytesIO
import zipfile
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# ================== Cấu hình ==================
API_KEY_REMOVE_BG = "8jhDKvHsX4JriY9B9dvvJQTb"
TEMP_IMAGE_LIFETIME = 5 * 60  # 5 phút
temp_images = {}  # Lưu (bytes, timestamp, is_video)
temp_videos = {}  # Tách riêng cho video để dễ quản lý

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_session'  # Cần cho session

# ================== Hỗ trợ ==================
def log(msg):
    print(f"🌀 {msg}")

def remove_bg(image_url, retries=3):
    for i in range(retries):
        try:
            response = requests.post(
                'https://api.remove.bg/v1.0/removebg',
                data={'image_url': image_url, 'size': 'auto'},
                headers={'X-Api-Key': API_KEY_REMOVE_BG},
                timeout=15
            )
            if response.status_code == 200:
                return response.content
            else:
                log(f"Lỗi Remove.bg ({response.status_code}): {response.text}")
        except requests.exceptions.RequestException as e:
            log(f"Lỗi request Remove.bg: {e}")
        time.sleep(1)
    raise Exception("Failed to remove background after retries.")

def download_pinterest_media(url, csrf):
    r = requests.post("https://klickpin.com/download", data={"url": url, "csrf_token": csrf})
    html = r.text

    match = re.search(r"downloadFile\('([^']+)'", html)
    if not match:
        raise Exception("Không tìm thấy link media.")
    media_url = match.group(1)
    if not media_url.startswith("http"):
        raise Exception("Link media không hợp lệ.")
    
    # Kiểm tra loại file
    parsed = urlparse(media_url)
    ext = parsed.path.split('.')[-1].lower() if '.' in parsed.path else ''
    is_video = ext in ['mp4', 'webm', 'mov', 'avi', 'mkv']
    if is_video:
        return media_url, True
    elif ext in ['jpg', 'jpeg', 'png', 'gif']:
        return media_url, False
    else:
        raise Exception("Không hỗ trợ loại media này (chỉ ảnh JPG/PNG/GIF và video MP4/WEBM/MOV/AVI/MKV).")

def download_media_bytes(media_url):
    response = requests.get(media_url, timeout=30)
    if response.status_code == 200:
        return response.content
    raise Exception(f"Lỗi tải media: {response.status_code}")

# ================== Cleanup thread ==================
def cleanup_temp_media():
    while True:
        now = time.time()
        keys_to_delete = [k for k, (_, ts) in temp_images.items() if now - ts > TEMP_IMAGE_LIFETIME]
        for k in keys_to_delete:
            print(f"🗑️ Xóa ảnh tạm: {k}")
            del temp_images[k]
        keys_to_delete_v = [k for k, (_, ts) in temp_videos.items() if now - ts > TEMP_IMAGE_LIFETIME]
        for k in keys_to_delete_v:
            print(f"🗑️ Xóa video tạm: {k}")
            del temp_videos[k]
        time.sleep(30)

Thread(target=cleanup_temp_media, daemon=True).start()

# ================== Routes ==================
@app.route("/", methods=["GET", "POST"])
def index():
    result_media = []  # list lưu tuples (filename, error_msg, is_video)
    error_msg = None

    if request.method == "POST":
        pinterest_text = request.form.get("urls", "").strip()
        if not pinterest_text:
            error_msg = "Vui lòng nhập ít nhất một link Pinterest!"
        else:
            # Tự động tách các link Pinterest từ text input
            pinterest_pattern = r'https?://(?:www\.)?pinterest\.(?:com|ca|uk|fr|de|jp|au|in)/pin/\d+/?'
            links = re.findall(pinterest_pattern, pinterest_text)
            if not links:
                error_msg = "Không tìm thấy link Pinterest hợp lệ trong input!"
            else:
                # Lấy CSRF token chỉ 1 lần cho toàn bộ request
                t = int(time.time() * 1000)
                csrf_resp = requests.get(f"https://klickpin.com/get-csrf-token.php?t={t}")
                csrf = csrf_resp.json().get("csrf_token") if csrf_resp.ok else None
                if not csrf:
                    error_msg = "Không lấy được CSRF token từ klickpin!"
                else:
                    pending = []
                    for link in links:
                        try:
                            media_url, is_video = download_pinterest_media(link, csrf)
                            pending.append((link, media_url, is_video))
                            log(f"📥 Đã lấy link {'video' if is_video else 'ảnh'} cho: {link[:50]}...")
                        except Exception as e:
                            result_media.append((None, f"{link}: {str(e)}", False))
                    
                    if pending:
                        # Xử lý song song cho tất cả pending, số luồng = số media
                        num_workers = len(pending)
                        result_names = []
                        with ThreadPoolExecutor(max_workers=num_workers) as executor:
                            futures = []
                            for plink, mediaurl, is_vid in pending:
                                if is_vid:
                                    # Với video: chỉ tải bytes gốc
                                    future = executor.submit(download_media_bytes, mediaurl)
                                else:
                                    # Với ảnh: xóa nền rồi tải bytes
                                    future = executor.submit(remove_bg, mediaurl)
                                futures.append((future, plink, is_vid, mediaurl))
                            
                            for future, plink, is_vid, mediaurl in futures:
                                try:
                                    media_bytes = future.result()
                                    i = len(result_names)  # Index theo thứ tự hoàn thành
                                    if is_vid:
                                        result_filename = f"video_{i}_{random.randint(1000,9999)}.mp4"
                                        temp_videos[result_filename] = (media_bytes, time.time())
                                    else:
                                        result_filename = f"removed_bg_{i}_{random.randint(1000,9999)}.png"
                                        temp_images[result_filename] = (media_bytes, time.time())
                                    result_media.append((result_filename, None, is_vid))
                                    result_names.append(result_filename)
                                    log(f"✅ Hoàn thành xử lý {'xóa nền' if not is_vid else 'tải'} cho {plink[:50]}...")
                                except Exception as e:
                                    result_media.append((None, f"{plink}: {str(e)}", is_vid))
                                    log(f"❌ Lỗi xử lý cho {plink}: {e}")
                        session['result_names'] = result_names
                    else:
                        error_msg = "Không thể lấy media từ bất kỳ Pinterest nào!"

    # Lấy result_names từ session nếu có
    result_names = session.get('result_names', [])

    html = """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>✨ Pinterest</title>
        <style>
            * { box-sizing: border-box; margin:0; padding:0; }
            body { 
                font-family: 'Segoe UI', sans-serif; 
                background: linear-gradient(135deg, #0f0f0f 0%, #1a1a1a 50%, #2d2d2d 100%); 
                color: #e0e0e0; 
                display:flex; 
                justify-content:center; 
                align-items:flex-start; 
                min-height:100vh; 
                padding:20px; 
            }
            .container { 
                width: 95%; 
                max-width:1200px; 
                background: #1e1e1e; 
                padding:40px; 
                border-radius:24px; 
                box-shadow:0 15px 50px rgba(0,0,0,0.5); 
                text-align:center; 
                border:1px solid #333; 
            }
            h1 { 
                color:#00b894; 
                margin-bottom:30px; 
                font-size:2.5em; 
                text-shadow:0 0 20px rgba(0,184,148,0.5); 
                letter-spacing:2px; 
            }
            h2 { 
                color:#fdcb6e; 
                margin:25px 0; 
                font-size:1.8em; 
            }
            form { margin-bottom:40px; }
            textarea { 
                width:100%; 
                padding:18px; 
                font-size:16px; 
                border-radius:16px; 
                border:2px solid #333; 
                background:#2a2a2a; 
                color:#e0e0e0; 
                resize:vertical; 
                min-height:140px; 
                transition: all 0.3s; 
            }
            textarea:focus { 
                border-color: #00b894; 
                outline: none; 
                box-shadow:0 0 15px rgba(0,184,148,0.3); 
            }
            button { 
                margin-top:15px; 
                padding:18px 35px; 
                font-size:18px; 
                background:linear-gradient(135deg, #00b894 0%, #00a085 100%); 
                color:white; 
                border:none; 
                border-radius:16px; 
                cursor:pointer; 
                transition: all 0.3s; 
                box-shadow:0 5px 20px rgba(0,184,148,0.4); 
                font-weight:bold; 
            }
            button:hover { 
                transform: translateY(-3px); 
                box-shadow:0 8px 25px rgba(0,184,148,0.5); 
            }
            .gallery { 
                display: grid; 
                grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); 
                gap: 25px; 
                margin-top:30px; 
            }
            .card { 
                background:linear-gradient(135deg, #2a2a2a 0%, #1f1f1f 100%); 
                padding:25px; 
                border-radius:20px; 
                box-shadow:0 8px 30px rgba(0,0,0,0.4); 
                transition: all 0.4s; 
                position:relative; 
                overflow:hidden; 
                border:1px solid #404040; 
            }
            .card:hover { 
                transform: translateY(-8px) scale(1.02); 
                box-shadow:0 15px 40px rgba(0,0,0,0.6); 
            }
            .card::before { 
                content: ''; 
                position: absolute; 
                top:0; 
                left:0; 
                right:0; 
                height:5px; 
                background:linear-gradient(90deg, #00b894, #00cec9); 
                opacity:0.8; 
            }
            .video-preview { 
                width:100%; 
                height:280px; 
                object-fit:cover; 
                border-radius:16px; 
                border:3px solid #333; 
                box-shadow:0 6px 20px rgba(0,0,0,0.3); 
                margin-bottom:18px; 
                transition: all 0.3s; 
            }
            .video-preview:hover { 
                box-shadow:0 10px 30px rgba(0,0,0,0.4); 
            }
            img { 
                width:100%; 
                height:280px; 
                object-fit:cover; 
                border-radius:16px; 
                border:3px solid #333; 
                box-shadow:0 6px 20px rgba(0,0,0,0.3); 
                margin-bottom:18px; 
                transition: all 0.3s; 
                cursor: pointer; 
            }
            img:hover { 
                transform: scale(1.05); 
                box-shadow:0 10px 30px rgba(0,0,0,0.4); 
            }
            .error { 
                color:#ff6b6b; 
                font-weight:bold; 
                margin-top:15px; 
                text-align:left; 
                background:#3a1a1a; 
                padding:15px; 
                border-radius:12px; 
                border-left:4px solid #ff6b6b; 
            }
            a.download-btn { 
                display:inline-block; 
                padding:15px 30px; 
                background:linear-gradient(135deg, #0984e3 0%, #0066cc 100%); 
                color:white; 
                border-radius:12px; 
                text-decoration:none; 
                font-weight:bold; 
                transition: all 0.3s; 
                box-shadow:0 4px 15px rgba(9,132,227,0.4); 
                margin-top:12px; 
            }
            a.download-btn:hover { 
                transform: translateY(-2px); 
                box-shadow:0 7px 20px rgba(9,132,227,0.5); 
            }
            a.download-all-btn { 
                display:inline-block; 
                padding:20px 45px; 
                background:linear-gradient(135deg, #6c5ce7 0%, #a29bfe 100%); 
                color:white; 
                border-radius:16px; 
                text-decoration:none; 
                font-weight:bold; 
                transition: all 0.3s; 
                box-shadow:0 6px 25px rgba(108,92,231,0.4); 
                margin:25px; 
                font-size:20px; 
            }
            a.download-all-btn:hover { 
                transform: translateY(-3px); 
                box-shadow:0 10px 30px rgba(108,92,231,0.5); 
            }
            .no-result { 
                color:#888; 
                font-style:italic; 
                margin-top:25px; 
                font-size:16px; 
            }
            /* Modal styles */
            .modal {
                display: none;
                position: fixed;
                z-index: 1000;
                left: 0;
                top: 0;
                width: 100%;
                height: 100%;
                overflow: auto;
                background-color: rgba(0,0,0,0.9);
            }
            .modal-content {
                margin: auto;
                display: block;
                width: 80%;
                max-width: 1200px;
                max-height: 90%;
                object-fit: contain;
                cursor: zoom-in;
                transition: transform 0.3s;
            }
            .modal-content.zoomed {
                cursor: zoom-out;
                transform: scale(2);
            }
            .video-modal {
                width: 80%;
                max-width: 1200px;
                max-height: 90%;
                margin: auto;
            }
            .close {
                position: absolute;
                top: 15px;
                right: 35px;
                color: #f1f1f1;
                font-size: 40px;
                font-weight: bold;
                cursor: pointer;
            }
            .close:hover {
                color: #00b894;
            }
            @media(max-width:768px){
                .gallery { grid-template-columns: 1fr; }
                .container { padding:25px; }
                h1 { font-size:2em; }
                h2 { font-size:1.5em; }
                .modal-content, .video-modal { width: 95%; }
            }
            @media(max-width:480px){
                .video-preview, img { height:220px; }
                button { padding:15px 25px; font-size:16px; }
                a.download-all-btn { padding:15px 30px; font-size:18px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🖼️ Pinterest (Ảnh/Video)</h1>
            <form method="post">
                <textarea name="urls" placeholder="Nhập 1 hoặc nhiều link Pinterest (hỗ trợ ảnh/video, dán liên tiếp tự tách)..." required>{{ request.form.get('urls','') if request.method == 'POST' else '' }}</textarea>
                <br>
                <button type="submit">🚀 Xử lý</button>
            </form>

            {% if error_msg %}
                <div class="error">{{ error_msg }}</div>
            {% endif %}

            {% if result_media %}
                <h2>✅ Đã xử lý {{ result_media|length }}!</h2>
                <a href="/download_all" class="download-all-btn">📦 Tải tất cả (ZIP)</a>
                <div class="gallery">
                    {% for filename, err, is_video in result_media %}
                        {% if err %}
                            <div class="card">
                                <div class="error" style="margin:0;">{{ err }}</div>
                            </div>
                        {% else %}
                            <div class="card">
                                {% if is_video %}
                                    <video class="video-preview" controls onclick="openVideoModal('/video/{{ filename }}')">
                                        <source src="/video/{{ filename }}" type="video/mp4">
                                        Trình duyệt không hỗ trợ video.
                                    </video>
                                {% else %}
                                    <img src="/image/{{ filename }}" alt="Preview" onclick="openModal('/image/{{ filename }}')">
                                {% endif %}
                                <br>
                                <a class="download-btn" href="{% if is_video %}/video/{% else %}/image/{% endif %}{{ filename }}" download="{{ filename }}">⬇️ Tải {{ 'Video' if is_video else 'Ảnh PNG' }}</a>
                            </div>
                        {% endif %}
                    {% endfor %}
                </div>
            {% elif request.method == "POST" %}
                <div class="no-result">Không có kết quả nào. Hãy thử lại!</div>
            {% endif %}
        </div>

        <!-- Modal cho ảnh -->
        <div id="imageModal" class="modal">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="modalImg" onclick="toggleZoom()">
        </div>

        <!-- Modal cho video -->
        <div id="videoModal" class="modal">
            <span class="close" onclick="closeVideoModal()">&times;</span>
            <video class="video-modal" controls id="modalVideo">
                <!-- Source sẽ set bằng JS -->
            </video>
        </div>

        <script>
            let isZoomed = false;
            function openModal(src) {
                const modal = document.getElementById('imageModal');
                const modalImg = document.getElementById('modalImg');
                modal.style.display = 'block';
                modalImg.src = src;
                isZoomed = false;
                modalImg.classList.remove('zoomed');
                document.body.style.overflow = 'hidden';
            }
            function closeModal() {
                document.getElementById('imageModal').style.display = 'none';
                document.body.style.overflow = 'auto';
            }
            function toggleZoom() {
                const modalImg = document.getElementById('modalImg');
                isZoomed = !isZoomed;
                modalImg.classList.toggle('zoomed');
            }
            function openVideoModal(src) {
                const modal = document.getElementById('videoModal');
                const modalVideo = document.getElementById('modalVideo');
                modalVideo.src = src;
                modal.style.display = 'block';
                document.body.style.overflow = 'hidden';
            }
            function closeVideoModal() {
                document.getElementById('videoModal').style.display = 'none';
                document.getElementById('modalVideo').pause(); // Dừng video
                document.body.style.overflow = 'auto';
            }
            // Đóng modal khi click ngoài
            window.onclick = function(event) {
                const imageModal = document.getElementById('imageModal');
                const videoModal = document.getElementById('videoModal');
                if (event.target == imageModal) closeModal();
                if (event.target == videoModal) closeVideoModal();
            }
            // Hỗ trợ zoom ảnh bằng wheel
            document.getElementById('imageModal').addEventListener('wheel', function(e) {
                e.preventDefault();
                toggleZoom();
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html, result_media=result_media, error_msg=error_msg)

@app.route("/image/<filename>")
def serve_image(filename):
    if filename in temp_images:
        data, _ = temp_images[filename]
        return send_file(BytesIO(data), mimetype="image/png", as_attachment=False)
    return "File not found", 404

@app.route("/video/<filename>")
def serve_video(filename):
    if filename in temp_videos:
        data, _ = temp_videos[filename]
        return send_file(BytesIO(data), mimetype="video/mp4", as_attachment=False)
    return "File not found", 404

@app.route("/download_all")
def download_all():
    result_names = session.get('result_names', [])
    if not result_names:
        return "No media to download", 404
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name in result_names:
            if name in temp_images:
                data, _ = temp_images[name]
                zip_file.writestr(name, data)
            elif name in temp_videos:
                data, _ = temp_videos[name]
                zip_file.writestr(name, data)
    
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="File.zip"
    )
