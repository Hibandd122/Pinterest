import time
import random
import re
import requests
from flask import Flask, request, render_template_string, send_file, session
from io import BytesIO
import zipfile
from threading import Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================== Cấu hình ==================
API_KEY_REMOVE_BG = "8jhDKvHsX4JriY9B9dvvJQTb"
TEMP_IMAGE_LIFETIME = 5 * 60  # 5 phút
temp_images = {}

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

def download_pinterest_img(url, csrf):
    r = requests.post("https://klickpin.com/download", data={"url": url, "csrf_token": csrf})
    html = r.text

    match = re.search(r"downloadFile\('([^']+)'", html)
    if not match:
        raise Exception("Không tìm thấy link ảnh/video.")
    img_url = match.group(1)
    if not img_url.startswith("http"):
        raise Exception("Link ảnh không hợp lệ.")
    if not img_url.endswith((".jpg", ".png")):
        raise Exception("Không phải ảnh, không thể xóa nền.")
    return img_url

# ================== Cleanup thread ==================
def cleanup_temp_images():
    while True:
        now = time.time()
        keys_to_delete = [k for k, (_, ts) in temp_images.items() if now - ts > TEMP_IMAGE_LIFETIME]
        for k in keys_to_delete:
            print(f"🗑️ Xóa ảnh tạm: {k}")
            del temp_images[k]
        time.sleep(30)

Thread(target=cleanup_temp_images, daemon=True).start()

# ================== Routes ==================
@app.route("/", methods=["GET", "POST"])
def index():
    result_imgs = []  # list lưu tuples (filename, error_msg)
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
                            img_url = download_pinterest_img(link, csrf)
                            pending.append((link, img_url))
                            log(f"📥 Đã lấy link ảnh cho: {link[:50]}...")
                        except Exception as e:
                            result_imgs.append((None, f"{link}: {str(e)}"))
                    
                    if pending:
                        # Tự động xóa nền song song cho tất cả pending, số luồng = số link
                        num_workers = len(pending)
                        result_names = []
                        with ThreadPoolExecutor(max_workers=num_workers) as executor:
                            future_to_plink = {executor.submit(remove_bg, imgurl): plink for plink, imgurl in pending}
                            for future in as_completed(future_to_plink):
                                plink = future_to_plink[future]
                                try:
                                    img_bytes = future.result()
                                    # Tìm index dựa trên plink (giả sử plink unique)
                                    i = next(idx for idx, (p, _) in enumerate(pending) if p == plink)
                                    result_img_name = f"removed_bg_{i}_{random.randint(1000,9999)}.png"
                                    temp_images[result_img_name] = (img_bytes, time.time())
                                    result_imgs.append((result_img_name, None))
                                    result_names.append(result_img_name)
                                    log(f"✅ Hoàn thành xóa nền cho {plink[:50]}...")
                                except Exception as e:
                                    result_imgs.append((None, f"{plink}: {str(e)}"))
                                    log(f"❌ Lỗi xóa nền cho {plink}: {e}")
                        session['result_names'] = result_names
                    else:
                        error_msg = "Không thể lấy link ảnh từ bất kỳ Pinterest nào!"

    # Lấy result_names từ session nếu có (nhưng thường thì không cần vì xử lý ngay)
    result_names = session.get('result_names', [])

    html = """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <title>✨ Xóa nền Pinterest</title>
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
                .modal-content { width: 95%; }
            }
            @media(max-width:480px){
                img { height:220px; }
                button { padding:15px 25px; font-size:16px; }
                a.download-all-btn { padding:15px 30px; font-size:18px; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🖼️ Xóa nền Pinterest</h1>
            <form method="post">
                <textarea name="urls" placeholder="Nhập 1 hoặc nhiều link Pinterest (hỗ trợ dán liên tiếp, tự động tách! Mỗi link 1 dòng hoặc dán sát nhau)..." required>{{ request.form.get('urls','') if request.method == 'POST' else '' }}</textarea>
                <br>
                <button type="submit">🚀 Xóa nền</button>
            </form>

            {% if error_msg %}
                <div class="error">{{ error_msg }}</div>
            {% endif %}

            {% if result_imgs %}
                <h2>✅ Đã xóa nền {{ result_imgs|length }} ảnh!</h2>
                <a href="/download_all" class="download-all-btn">📦 Tải tất cả ảnh (ZIP)</a>
                <div class="gallery">
                    {% for filename, err in result_imgs %}
                        {% if err %}
                            <div class="card">
                                <div class="error" style="margin:0;">{{ err }}</div>
                            </div>
                        {% else %}
                            <div class="card">
                                <img src="/image/{{ filename }}" alt="Preview ảnh xóa nền" onclick="openModal('/image/{{ filename }}')">
                                <br>
                                <a class="download-btn" href="/image/{{ filename }}" download="{{ filename }}">⬇️ Tải ảnh PNG</a>
                            </div>
                        {% endif %}
                    {% endfor %}
                </div>
            {% elif request.method == "POST" %}
                <div class="no-result">Không có kết quả nào. Hãy thử lại!</div>
            {% endif %}
        </div>

        <!-- Modal -->
        <div id="imageModal" class="modal">
            <span class="close" onclick="closeModal()">&times;</span>
            <img class="modal-content" id="modalImg" onclick="toggleZoom()">
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
                document.body.style.overflow = 'hidden'; // Ngăn scroll body
            }
            function closeModal() {
                const modal = document.getElementById('imageModal');
                modal.style.display = 'none';
                document.body.style.overflow = 'auto'; // Cho phép scroll lại
            }
            function toggleZoom() {
                const modalImg = document.getElementById('modalImg');
                isZoomed = !isZoomed;
                modalImg.classList.toggle('zoomed');
            }
            // Đóng modal khi click ngoài ảnh
            window.onclick = function(event) {
                const modal = document.getElementById('imageModal');
                if (event.target == modal) {
                    closeModal();
                }
            }
            // Hỗ trợ zoom bằng wheel (scroll)
            document.getElementById('imageModal').addEventListener('wheel', function(e) {
                e.preventDefault();
                toggleZoom(); // Toggle zoom khi scroll wheel
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html, result_imgs=result_imgs, error_msg=error_msg)

@app.route("/image/<filename>")
def serve_image(filename):
    if filename in temp_images:
        data, _ = temp_images[filename]
        return send_file(BytesIO(data), mimetype="image/png", as_attachment=False)
    return "File not found", 404

@app.route("/download_all")
def download_all():
    result_names = session.get('result_names', [])
    if not result_names:
        return "No images to download", 404
    
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for name in result_names:
            if name in temp_images:
                data, _ = temp_images[name]
                zip_file.writestr(name, data)
    
    zip_buffer.seek(0)
    return send_file(
        zip_buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="removed_backgrounds_all.zip"
    )
