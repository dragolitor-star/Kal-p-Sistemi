import streamlit as st
import cv2
import numpy as np
import ezdxf
import tempfile
import os
import time
from datetime import datetime
import json
import base64
from PIL import Image
import io

# --- FIREBASE BAŞLATMA ---
import firebase_admin
from firebase_admin import credentials, firestore

# NOT: Storage bucket kullanımı kaldırıldı.
# Sadece Firestore (Veritabanı) kullanılacak.

if not firebase_admin._apps:
    # YÖNTEM 1: Streamlit Cloud Secrets (Github Dağıtımı İçin)
    if 'firebase' in st.secrets:
        key_dict = dict(st.secrets["firebase"])
        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred) # storageBucket parametresi kaldırıldı
        
    # YÖNTEM 2: Yerel Dosya (Local Test İçin)
    elif os.path.exists("serviceAccountKey.json"):
        cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(cred) # storageBucket parametresi kaldırıldı
    else:
        st.error("Firebase kimlik bilgileri bulunamadı! Lütfen Secrets ayarlarını yapın veya serviceAccountKey.json dosyasını ekleyin.")
        st.stop()

db = firestore.client()

# --- SAYFA AYARLARI ---
st.set_page_config(
    page_title="Lazer Kalıp Yönetim Sistemi",
    page_icon="✂️",
    layout="wide"
)

# --- YARDIMCI FONKSİYONLAR ---

def compress_image_to_base64(uploaded_file):
    """
    Firestore 1MB limitine takılmamak için resmi küçültür ve Base64'e çevirir.
    """
    image = Image.open(uploaded_file)
    
    # Resmi RGB'ye çevir (PNG transparanlık sorununu önlemek için)
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
        
    # Boyutlandırma (Max 1024px)
    image.thumbnail((1024, 1024))
    
    # JPEG olarak sıkıştır
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='JPEG', quality=60) # %60 kalite genelde yeterlidir
    img_byte_arr = img_byte_arr.getvalue()
    
    # Base64'e çevir
    base64_str = base64.b64encode(img_byte_arr).decode('utf-8')
    return base64_str

def base64_to_image(base64_str):
    """Base64 stringini PIL Image objesine çevirir."""
    img_data = base64.b64decode(base64_str)
    return Image.open(io.BytesIO(img_data))

def process_base64_to_dxf(base64_img_str):
    """
    Base64 formatındaki görseli işler ve DXF Base64 verisi döndürür.
    """
    # 1. Base64'ten OpenCV formatına çevir
    img_data = base64.b64decode(base64_img_str)
    nparr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        raise ValueError("Görüntü okunamadı.")

    # Görüntü işleme adımları
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    ret, thresh = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # 2. DXF Oluşturma
    doc = ezdxf.new()
    msp = doc.modelspace()
    
    for contour in contours:
        if cv2.contourArea(contour) < 100:
            continue
        points = [(float(pt[0][0]), float(-pt[0][1])) for pt in contour]
        if len(points) > 2:
            msp.add_lwpolyline(points, close=True)
            
    # 3. Geçici dosyaya kaydetmeden bellekte byte olarak al
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dxf") as tmp:
        doc.saveas(tmp.name)
        tmp.seek(0)
        dxf_bytes = tmp.read()
        
    os.unlink(tmp.name)
    
    # DXF'i Base64 string'e çevirip döndür
    return base64.b64encode(dxf_bytes).decode('utf-8')

# --- VERİTABANI YÖNETİCİSİ (DB MANAGER) ---
class DataManager:
    """Storage kullanmadan sadece Firestore işlemleri."""
    
    @staticmethod
    def add_request(user_email, description, image_file):
        status = "Onay Bekliyor"
        created_at = datetime.now()
        
        # Resmi sıkıştır ve base64 string olarak al
        image_base64 = compress_image_to_base64(image_file)
        
        # Veritabanına yaz (Resim verisi doğrudan 'image_data' alanına yazılıyor)
        doc_ref = db.collection('requests').document()
        doc_ref.set({
            'requester': user_email,
            'description': description,
            'image_data': image_base64, # URL yerine veri
            'status': status,
            'created_at': created_at,
            'dxf_data': None
        })

    @staticmethod
    def get_requests():
        docs = db.collection('requests').order_by('created_at', direction=firestore.Query.DESCENDING).stream()
        return [{"id": doc.id, **doc.to_dict()} for doc in docs]

    @staticmethod
    def update_status_and_dxf(req_id, new_status, dxf_base64=None):
        doc_ref = db.collection('requests').document(req_id)
        update_data = {'status': new_status}
        
        if dxf_base64:
            # DXF verisini de doğrudan veritabanına yaz
            update_data['dxf_data'] = dxf_base64
            
        doc_ref.update(update_data)

# --- ARAYÜZ (UI) ---

def sidebar_menu():
    st.sidebar.title("Lazer Yönetim v1.0")
    
    if firebase_admin._apps:
        st.sidebar.success("🟢 Firestore Bağlı")
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
                with st.spinner("Dosya sıkıştırılıyor ve kaydediliyor..."):
                    try:
                        DataManager.add_request(email, desc, uploaded_file)
                        st.success("Talep başarıyla oluşturuldu! Operatör onayına düştü.")
                    except Exception as e:
                        st.error(f"Hata oluştu: {str(e)}")
            else:
                st.error("Lütfen e-posta girin ve bir dosya yükleyin.")

    st.divider()
    st.subheader("📋 Taleplerim")
    requests_data = DataManager.get_requests()
    
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
                img_data = req.get('image_data')
                if img_data:
                    try:
                        image = base64_to_image(img_data)
                        st.image(image, use_container_width=True)
                    except:
                        st.error("Görsel yüklenemedi.")
            
            with col2:
                st.info(f"Talep Eden: {req.get('requester')}")
                st.write(f"Durum: **{req.get('status')}**")
                
                # --- DURUM 1: ONAY VE DXF OLUŞTURMA ---
                if req.get('status') == "Onay Bekliyor":
                    if st.button("✅ Onayla ve DXF Oluştur", key=f"btn_dxf_{req['id']}"):
                        with st.spinner("Görüntü işleniyor..."):
                            try:
                                # Doğrudan Base64 verisi üzerinden işlem yapıyoruz
                                dxf_base64 = process_base64_to_dxf(req.get('image_data'))
                                
                                DataManager.update_status_and_dxf(req['id'], "Hazırlık (DXF İndirilebilir)", dxf_base64)
                                st.success("DXF veritabanına kaydedildi!")
                                time.sleep(1)
                                st.rerun()
                            except Exception as e:
                                st.error(f"Bir hata oluştu: {str(e)}")

                # --- DURUM 2: İŞLEME VE TAMAMLAMA ---
                elif "Hazırlık" in req.get('status'):
                    st.success("DXF Dosyası Hazır.")
                    
                    dxf_data = req.get('dxf_data')
                    if dxf_data:
                        # Base64 string'i byte'a çevirip indirilebilir yapıyoruz
                        dxf_bytes = base64.b64decode(dxf_data)
                        
                        st.download_button(
                            label="⬇️ DXF Dosyasını İndir",
                            data=dxf_bytes,
                            file_name=f"kalip_{req['id']}.dxf",
                            mime="application/dxf"
                        )
                        
                        st.markdown("---")
                        st.write("👉 **Operatör Talimatı:** Dosyayı indirin, RDWorks'te lazer ayarlarını yapın.")
                        
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
