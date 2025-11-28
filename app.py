import streamlit as st
import cv2
import numpy as np
import ezdxf
import tempfile
import os
import time
from datetime import datetime
import requests
import json # JSON işlemleri için

# --- FIREBASE BAŞLATMA ---
import firebase_admin
from firebase_admin import credentials, firestore, storage

# DİKKAT: Buraya kendi Storage Bucket adınızı yazmalısınız.
FIREBASE_STORAGE_BUCKET = 'SİZİN_STORAGE_BUCKET_ADINIZ.appspot.com'

if not firebase_admin._apps:
    # YÖNTEM 1: Streamlit Cloud Secrets (Github Dağıtımı İçin)
    # Streamlit Cloud'da 'Secrets' bölümüne tanımlanan bilgileri kullanır.
    if 'firebase' in st.secrets:
        # st.secrets bir AttrDict döner, bunu normal dict'e çevirip sertifika oluşturuyoruz
        key_dict = dict(st.secrets["firebase"])
        
        # Private key içindeki \n karakterlerini düzeltmek gerekebilir
        # (Streamlit bazen string olarak okurken kaçış karakterlerini değiştirebilir)
        # key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
        
        cred = credentials.Certificate(key_dict)
        
        firebase_admin.initialize_app(cred, {
            'storageBucket': FIREBASE_STORAGE_BUCKET
        })
        
    # YÖNTEM 2: Yerel Dosya (Local Test İçin)
    # Eğer secrets yoksa ve yerelde serviceAccountKey.json varsa onu kullanır.
    elif os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred, {
            'storageBucket': FIREBASE_STORAGE_BUCKET
        })
    else:
        st.error("Firebase kimlik bilgileri bulunamadı! Lütfen Secrets ayarlarını yapın veya serviceAccountKey.json dosyasını ekleyin.")
        st.stop()

db = firestore.client()
bucket = storage.bucket()

# --- SAYFA AYARLARI ---
st.set_page_config(
    page_title="Lazer Kalıp Yönetim Sistemi",
    page_icon="✂️",
    layout="wide"
)

# --- YARDIMCI FONKSİYONLAR (GÖRÜNTÜ İŞLEME) ---
def process_image_to_dxf(image_bytes):
    """
    Byte formatındaki görsel verisini işler ve DXF byte verisi döndürür.
    """
    # 1. Byte verisini OpenCV formatına çevir
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        raise ValueError("Görüntü okunamadı.")

    # Görüntü işleme adımları (Gri ton -> Blur -> Kenar Bulma)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    
    # Otomatik eşikleme (Otsu's method) veya Canny
    ret, thresh = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # 2. DXF Oluşturma
    doc = ezdxf.new()
    msp = doc.modelspace()
    
    for contour in contours:
        # Gürültü filtreleme (çok küçük alanları atla)
        if cv2.contourArea(contour) < 100:
            continue
            
        # Noktaları DXF formatına uygun listeye çevir
        # Not: DXF'de Y ekseni yukarı bakar, resimde aşağı. Y'yi ters çeviriyoruz.
        points = [(float(pt[0][0]), float(-pt[0][1])) for pt in contour]
        
        # Çizgiyi kapat (closed loop)
        if len(points) > 2:
            msp.add_lwpolyline(points, close=True)
            
    # 3. Geçici dosyaya kaydetmeden bellekte byte olarak döndür
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        doc.saveas(tmp.name)
        tmp.seek(0)
        dxf_data = tmp.read()
        
    # Geçici dosyayı temizle
    os.unlink(tmp.name)
    
    return dxf_data

# --- VERİTABANI YÖNETİCİSİ (DB MANAGER) ---
class DataManager:
    """Sadece Firebase işlemleri."""
    
    @staticmethod
    def add_request(user_email, description, image_file):
        status = "Onay Bekliyor"
        created_at = datetime.now()
        
        # 1. Resmi Storage'a yükle
        # Benzersiz bir dosya adı oluştur
        filename = f"{int(time.time())}_{image_file.name}"
        blob = bucket.blob(f"uploads/{filename}")
        
        blob.upload_from_file(image_file, content_type=image_file.type)
        blob.make_public()
        image_url = blob.public_url
        
        # 2. Veritabanına yaz
        doc_ref = db.collection('requests').document()
        doc_ref.set({
            'requester': user_email,
            'description': description,
            'image_url': image_url,
            'status': status,
            'created_at': created_at,
            'dxf_url': None
        })

    @staticmethod
    def get_requests():
        # Tarihe göre tersten sırala (en yeni en üstte)
        docs = db.collection('requests').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]

    @staticmethod
    def update_status_and_dxf(req_id, new_status, dxf_bytes=None):
        doc_ref = db.collection('requests').document(req_id)
        update_data = {'status': new_status}
        
        if dxf_bytes:
            # DXF dosyasını Storage'a yükle
            blob_path = f"generated/{req_id}.dxf"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(dxf_bytes, content_type="application/dxf")
            blob.make_public()
            update_data['dxf_url'] = blob.public_url
            
        doc_ref.update(update_data)

# --- ARAYÜZ (UI) ---

def sidebar_menu():
    st.sidebar.title("Lazer Yönetim v1.0")
    
    # Bağlantı durumu kontrolü
    if firebase_admin._apps:
        st.sidebar.success("🟢 Firebase Bağlı")
    else:
        st.sidebar.error("🔴 Bağlantı Hatası")
        
    role = st.sidebar.radio("Kullanıcı Rolü Seçin", ["Business Unit (Talep)", "Operatör (Üretim)"])
    return role

def business_unit_view():
    st.title("📝 Yeni Kalıp Talebi Oluştur")
    st.markdown("Lütfen kesim yapılacak etiket görselini yükleyiniz.")
    
    with st.form("request_form"):
        email = st.text_input("E-posta Adresiniz", "talep@firma.com")
        desc = st.text_area("Talep Açıklaması", "Örn: X-500 Etiket Kalıbı - Pleksi Malzeme")
        uploaded_file = st.file_uploader("Etiket Görseli (JPG/PNG)", type=['png', 'jpg', 'jpeg'])
        
        submitted = st.form_submit_button("Talebi Gönder")
        
        if submitted:
            if uploaded_file and email:
                uploaded_file.seek(0)
                with st.spinner("Dosya yükleniyor..."):
                    DataManager.add_request(email, desc, uploaded_file)
                st.success("Talep başarıyla oluşturuldu! Operatör onayına düştü.")
            else:
                st.error("Lütfen e-posta girin ve bir dosya yükleyin.")

    st.divider()
    st.subheader("📋 Taleplerim")
    requests_data = DataManager.get_requests()
    
    # Basit tablo gösterimi
    if requests_data:
        st.dataframe([
            {"Tarih": r.get('created_at'), "Açıklama": r.get('description'), "Durum": r.get('status')} 
            for r in requests_data
        ], use_container_width=True)
    else:
        st.info("Henüz bir talep bulunmuyor.")

def operator_view():
    st.title("⚙️ Operatör Paneli")
    
    all_requests = DataManager.get_requests()
    pending = [r for r in all_requests if r.get('status') != 'Tamamlandı']
    
    st.metric("Bekleyen İşler", len(pending))
    
    if not pending:
        st.success("Bekleyen iş yok, her şey yolunda! 👍")
        return

    for req in pending:
        with st.expander(f"Talep #{req['id'][:5]}.. - {req.get('description')} ({req.get('status')})", expanded=True):
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.caption("Gelen Görsel:")
                st.image(req.get('image_url'), use_container_width=True)
            
            with col2:
                st.info(f"Talep Eden: {req.get('requester')}")
                st.write(f"Durum: **{req.get('status')}**")
                
                # --- DURUM 1: ONAY VE DXF OLUŞTURMA ---
                if req.get('status') == "Onay Bekliyor":
                    if st.button("✅ Onayla ve DXF Oluştur", key=f"btn_dxf_{req['id']}"):
                        with st.spinner("Görüntü indiriliyor ve vektöre çevriliyor..."):
                            try:
                                # 1. Storage'dan resmi indir
                                response = requests.get(req.get('image_url'))
                                if response.status_code == 200:
                                    image_bytes = response.content
                                    
                                    # 2. İşle
                                    dxf_bytes = process_image_to_dxf(image_bytes)
                                    
                                    # 3. Güncelle
                                    DataManager.update_status_and_dxf(req['id'], "Hazırlık (DXF İndirilebilir)", dxf_bytes)
                                    st.success("DXF başarıyla oluşturuldu ve yüklendi!")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error("Görüntü indirilemedi. Bağlantıyı kontrol edin.")
                            except Exception as e:
                                st.error(f"Bir hata oluştu: {str(e)}")

                # --- DURUM 2: İŞLEME VE TAMAMLAMA ---
                elif "Hazırlık" in req.get('status'):
                    st.success("DXF Dosyası Hazır.")
                    
                    dxf_url = req.get('dxf_url')
                    if dxf_url:
                        st.link_button("⬇️ DXF Dosyasını İndir (Tarayıcıda Aç)", dxf_url)
                        
                        st.markdown("---")
                        st.write("👉 **Operatör Talimatı:** Dosyayı indirin, RDWorks'te lazer ayarlarını yapın ve makineye gönderin.")
                        
                        if st.button("🏁 Üretimi Tamamla ve Arşivle", key=f"btn_fin_{req['id']}"):
                            DataManager.update_status_and_dxf(req['id'], "Tamamlandı")
                            st.success("İşlem tamamlandı!")
                            time.sleep(1)
                            st.rerun()

# --- ANA AKIŞ ---
def main():
    role = sidebar_menu()
    
    if role == "Business Unit (Talep)":
        business_unit_view()
    elif role == "Operatör (Üretim)":
        operator_view()

if __name__ == "__main__":
    main()
