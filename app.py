import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------------------------------
# 1. AYARLAR VE FIREBASE BAĞLANTISI (SECRETS ENTEGRASYONU)
# --------------------------------------------------------------------------
st.set_page_config(page_title="Gerber vs Polypattern Kontrol", layout="wide")

# Firebase başlatma (Secrets kullanarak)
# Streamlit Cloud'da "Secrets" kısmından, Local'de ".streamlit/secrets.toml" dosyasından okur.
if not firebase_admin._apps:
    try:
        # Secrets verisini dictionary olarak al
        key_dict = dict(st.secrets["firebase"])
        
        # Private key içindeki "\n" karakterleri string olarak gelebilir, 
        # onları gerçek satır başı karakterine çevirmemiz gerekir.
        if "private_key" in key_dict:
            key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

        cred = credentials.Certificate(key_dict)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Firestore bağlantı hatası: {e}. Lütfen Secrets ayarlarını kontrol edin.")

# DB İstemcisi
try:
    db = firestore.client()
except:
    db = None # DB bağlantısı yoksa uygulama hata vermeden demo modunda çalışsın

# --------------------------------------------------------------------------
# 2. PARSER FONKSİYONLARI (METİN İŞLEME)
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    """
    Gerber çıktısındaki (L1/UTJW-DW0DW22280-SP26-OBAS) formatından
    Model, Sezon ve Parça ismini çeker.
    """
    # Regex: L1/ sonrasındaki kodları yakalar.
    # UTJW-DW0DW22280 (Model), SP26 (Sezon: 2 harf 2 rakam), OBAS (Parça)
    pattern = r"L\d+\/([\w-]+)-([A-Z]{2}\d{2})-([A-Z0-9]+)"
    match = re.search(pattern, text_block)
    
    if match:
        return {
            "model_adi": match.group(1),
            "sezon": match.group(2),
            "parca_adi": match.group(3)
        }
    return None

def clean_number(val):
    """Virgüllü sayıları float'a çevirir."""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        # Virgülü noktaya çevir ve sayı dışındaki karakterleri temizle (bazı durumlarda)
        val = str(val).replace(',', '.')
        # Regex ile sadece sayısal değeri çek (negatifler dahil)
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found:
            return float(found[0])
        return 0.0
    except:
        return 0.0

def parse_gerber_table(text, value_type):
    """
    Gerber'den kopyalanan metni tabloya çevirir.
    value_type: 'cevre', 'en', 'boy'
    """
    lines = text.strip().split('\n')
    data = []
    
    # Beden regex'i: Başta XXS, XS, S, *S, M, L, XL, XXL vb. yakalar.
    size_pattern = r"^(\*?[A-Z0-9]+)\s+(.*)" 

    for line in lines:
        line = line.strip()
        match = re.match(size_pattern, line)
        if match:
            beden = match.group(1).replace("*", "") # *S'i S yap
            rest = match.group(2)
            
            # Sayıları ayır (boşluk veya tab ile ayrılmış varsayıyoruz)
            numbers = re.split(r'\s+', rest)
            
            try:
                val = 0.0
                if value_type == 'cevre':
                    # Çevre tablosunda "Toplam" genellikle sondan 2. değerdir (Beden Farkı'ndan önce).
                    val = clean_number(numbers[-2]) 
                elif value_type == 'en':
                    # En tablosunda Y Mesafe (Genellikle 3. veya 4. blok)
                    # Örnek: XXS 50,84(X) 50,1(XFark) -8,64(Y) ...
                    val = clean_number(numbers[3]) if len(numbers) > 3 else 0.0
                elif value_type == 'boy':
                    # Boy tablosunda X Mesafe.
                    val = clean_number(numbers[1]) if len(numbers) > 1 else 0.0
                
                data.append({"Beden": beden, value_type: val})
            except:
                continue

    return pd.DataFrame(data)

def parse_polypattern(text):
    """
    Polypattern temiz tablosunu işler.
    Format: UTJW... Boy En Çevre
            XXS 50,1 31,99 163,49
    """
    lines = text.strip().split('\n')
    data = []
    
    for line in lines:
        parts = re.split(r'\s+', line.strip())
        # En az 4 eleman olmalı: Beden, Boy, En, Çevre
        if len(parts) >= 4:
            # İlk elemanın beden olup olmadığını kontrol et (Sayı ile başlamamalı)
            if not parts[0][0].isdigit():
                try:
                    beden = parts[0].replace("*", "")
                    boy = clean_number(parts[1])
                    en = clean_number(parts[2])
                    cevre = clean_number(parts[3])
                    
                    data.append({
                        "Beden": beden,
                        "poly_boy": boy,
                        "poly_en": en,
                        "poly_cevre": cevre
                    })
                except:
                    continue
    return pd.DataFrame(data)

# --------------------------------------------------------------------------
# 3. SAYFA DÜZENİ VE AKIŞ
# --------------------------------------------------------------------------

def main():
    # Session State Tanımları
    if 'current_model' not in st.session_state:
        st.session_state['current_model'] = {}
    if 'model_parts' not in st.session_state:
        st.session_state['model_parts'] = [] 

    st.title("🏭 Kalıp Ölçü Kontrol Sistemi")
    st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3022/3022329.png", width=100)
    
    # Giriş Simülasyonu
    user = st.sidebar.text_input("Kullanıcı Adı", "muhendis_user")
    
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü":
        new_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü")

    # Adım 1: Model Başlatma ve Bilgiler
    with st.expander("ℹ️ İşlem Bilgisi", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"])
        
        # Eğer aktif bir oturum varsa bilgileri göster
        if st.session_state.get('active_session'):
            st.info(f"Aktif Model: **{st.session_state['current_model'].get('model_adi')}** | Sezon: **{st.session_state['current_model'].get('sezon')}**")
            st.write(f"Şu ana kadar eklenen parça sayısı: {len(st.session_state['model_parts'])}")

    st.divider()

    col_gerber, col_poly = st.columns([1, 1])

    # --- INPUT ALANLARI ---
    # Not: text_area key'leri her parça kaydında temizlenmeli, bunun için form kullanmıyoruz
    # ancak kayıttan sonra st.rerun ile state temizleyebiliriz.
    
    with col_gerber:
        st.subheader("1. Gerber Verileri")
        st.caption("Sırasıyla Çevre, En ve Boy tablolarını yapıştırın.")
        g_cevre_txt = st.text_area("Gerber Çevre Tablosu", height=100)
        g_en_txt = st.text_area("Gerber En Tablosu (Y Mesafe)", height=100)
        g_boy_txt = st.text_area("Gerber Boy Tablosu (X Mesafe)", height=100)

    with col_poly:
        st.subheader("2. Polypattern Verisi")
        st.caption("Polypattern programından alınan toplu tabloyu yapıştırın.")
        poly_txt = st.text_area("Polypattern Çıktısı", height=340)

    # --- ANALİZ BUTONU ---
    if st.button("Ölçüleri Karşılaştır", type="primary"):
        if not (g_cevre_txt and g_en_txt and g_boy_txt and poly_txt):
            st.warning("Lütfen tüm alanları doldurunuz.")
            return

        # 1. Metadata Çıkarma (Sadece ilk tablodan)
        metadata = parse_gerber_metadata(g_cevre_txt)
        if metadata:
            current_model_info = {
                "model_adi": metadata['model_adi'],
                "sezon": metadata['sezon'],
                "parca_adi": metadata['parca_adi'],
                "bu": business_unit
            }
            # Session başlatma veya güncelleme
            if 'active_session' not in st.session_state:
                st.session_state['active_session'] = True
                st.session_state['current_model'] = current_model_info
            else:
                # Model adı değişmemeli ama parça adı güncellenmeli
                st.session_state['current_model']['parca_adi'] = metadata['parca_adi']
        else:
            st.error("Gerber verisinden Model/Sezon bilgisi okunamadı. Formatı kontrol edin.")
            return

        # 2. Tabloları İşleme
        df_g_cevre = parse_gerber_table(g_cevre_txt, 'cevre')
        df_g_en = parse_gerber_table(g_en_txt, 'en')
        df_g_boy = parse_gerber_table(g_boy_txt, 'boy')
        df_poly = parse_polypattern(poly_txt)

        if df_g_cevre.empty or df_g_en.empty or df_g_boy.empty or df_poly.empty:
            st.error("Veriler tabloya dönüştürülemedi. Lütfen kopyalama formatını kontrol edin.")
            return

        # 3. Birleştirme ve Hesaplama
        try:
            df_gerber_total = df_g_cevre.merge(df_g_en, on="Beden").merge(df_g_boy, on="Beden")
            df_final = df_gerber_total.merge(df_poly, on="Beden", how="inner")
            
            df_final['Fark_Boy'] = df_final['boy'] - df_final['poly_boy']
            df_final['Fark_En'] = df_final['en'] - df_final['poly_en']
            df_final['Fark_Cevre'] = df_final['cevre'] - df_final['poly_cevre']

            # Sonuçları geçici state'e at (Kayıt butonu için)
            st.session_state['last_analysis'] = df_final
            
        except Exception as e:
            st.error(f"Tablo birleştirme hatası: {e}")
            return

    # --- SONUÇLARI GÖSTERME VE KAYDETME ---
    if 'last_analysis' in st.session_state and st.session_state['last_analysis'] is not None:
        df_final = st.session_state['last_analysis']
        tolerans = 0.05
        
        hatali_satirlar = df_final[
            (df_final['Fark_Boy'].abs() > tolerans) | 
            (df_final['Fark_En'].abs() > tolerans) | 
            (df_final['Fark_Cevre'].abs() > tolerans)
        ]
        
        hata_var = not hatali_satirlar.empty

        st.divider()
        st.subheader(f"Sonuçlar: {st.session_state['current_model'].get('parca_adi', 'Bilinmeyen Parça')}")
        
        # --- TABLO GÖSTERİMİ DÜZELTİLDİ ---
        # Sayısal olmayan "Beden" sütununun format hatası vermemesi için
        # sadece sayısal sütunları seçiyoruz.
        
        numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
        existing_numeric_cols = [col for col in numeric_cols if col in df_final.columns]

        st.dataframe(
            df_final.style
            .format("{:.2f}", subset=existing_numeric_cols) # Sadece sayılara format uygula
            .map(
                lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
            )
        )

        if hata_var:
            st.error(f"⚠️ DİKKAT: {len(hatali_satirlar)} bedende ölçü farkı tespit edildi!")
        else:
            st.success("✅ Tüm ölçüler tolerans dahilinde uyumlu.")

        # Parça Kaydetme Butonu
        col_btn1, col_btn2 = st.columns([1, 4])
        with col_btn1:
            if st.button("💾 Parçayı Listeye Ekle"):
                part_record = {
                    "parca_adi": st.session_state['current_model']['parca_adi'],
                    "durum": "Hatalı" if hata_var else "Doğru",
                    "hata_detayi": hatali_satirlar[['Beden', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']].to_dict('records') if hata_var else [],
                    "timestamp": datetime.now()
                }
                st.session_state['model_parts'].append(part_record)
                
                # Ekranı temizle (Analiz verisini sil)
                del st.session_state['last_analysis']
                st.success("Parça eklendi! Yeni parça için yukarıdaki alanları temizleyip yapıştırabilirsiniz.")
                st.rerun()

    # --- MODELİ BİTİRME BUTONU ---
    if st.session_state.get('active_session') and len(st.session_state['model_parts']) > 0:
        st.markdown("---")
        st.subheader("Model İşlemleri")
        if st.button("🏁 Tüm Model Kontrolünü Tamamla ve Veritabanına Yaz", type="primary"):
            save_to_firestore(user, business_unit)

def save_to_firestore(user, bu):
    if not db:
        st.warning("Veritabanı bağlantısı yok (Secrets yapılandırılmamış olabilir).")
        # State temizle
        st.session_state['model_parts'] = []
        st.session_state['current_model'] = {}
        del st.session_state['active_session']
        if 'last_analysis' in st.session_state: del st.session_state['last_analysis']
        return

    model_data = st.session_state['current_model']
    parts = st.session_state['model_parts']
    
    # Genel durum tespiti
    genel_durum = "Doğru Çevrilmiş"
    for p in parts:
        if p['durum'] == "Hatalı":
            genel_durum = "Hatalı"
            break
            
    doc_ref = db.collection('qc_records').document()
    doc_ref.set({
        'kullanici': user,
        'tarih': datetime.now(),
        'business_unit': bu,
        'model_adi': model_data.get('model_adi'),
        'sezon': model_data.get('sezon'),
        'parca_sayisi': len(parts),
        'genel_durum': genel_durum,
        'parca_detaylari': parts
    })
    
    st.balloons()
    st.success("Model başarıyla kaydedildi!")
    
    # State temizle
    st.session_state['model_parts'] = []
    st.session_state['current_model'] = {}
    del st.session_state['active_session']
    if 'last_analysis' in st.session_state: del st.session_state['last_analysis']
    st.rerun()

def history_page():
    st.header("📋 Model Kontrol Listesi")
    
    if not db:
        st.warning("Veritabanı bağlı değil.")
        return

    col1, col2 = st.columns(2)
    search_term = col1.text_input("Model veya Kullanıcı Ara")
    
    # Veriyi çek
    try:
        docs = db.collection('qc_records').order_by('tarih', direction=firestore.Query.DESCENDING).limit(50).stream()
        
        data = []
        for doc in docs:
            d = doc.to_dict()
            data.append(d)
            
        df = pd.DataFrame(data)
        
        if not df.empty:
            # Timestamp düzeltme
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih']).dt.strftime('%Y-%m-%d %H:%M')
            
            # Arama filtresi
            if search_term:
                df = df[df['model_adi'].str.contains(search_term, case=False, na=False) | 
                        df['kullanici'].str.contains(search_term, case=False, na=False)]

            st.dataframe(
                df[['tarih', 'kullanici', 'business_unit', 'model_adi', 'sezon', 'genel_durum', 'parca_sayisi']],
                use_container_width=True
            )
            
            # Detay Gösterme Opsiyonu
            selected_row = st.selectbox("Detaylarını görmek istediğiniz modeli seçin:", df['model_adi'].unique())
            if selected_row:
                detay = df[df['model_adi'] == selected_row].iloc[0]
                st.write(f"**Parça Detayları ({selected_row}):**")
                st.json(detay['parca_detaylari'])
                
        else:
            st.info("Henüz kayıt bulunmamaktadır.")
            
    except Exception as e:
        st.error(f"Veri çekilirken hata oluştu: {e}")

if __name__ == "__main__":
    main()
