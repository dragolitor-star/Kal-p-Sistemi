import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------------------------------
# 1. AYARLAR VE GÜVENLİ FIREBASE BAĞLANTISI
# --------------------------------------------------------------------------
st.set_page_config(page_title="Gerber vs Polypattern Kontrol", layout="wide")

# Firebase Başlatma (GitHub uyumlu - Secrets kullanımı)
if not firebase_admin._apps:
    try:
        # Streamlit secrets'tan veriyi al
        # secrets.toml dosyasındaki [firebase] başlığı altındaki verileri okur
        if "firebase" in st.secrets:
            key_dict = dict(st.secrets["firebase"])
            
            # Private key içindeki "\n" kaçış karakterlerini düzelt
            if "private_key" in key_dict:
                key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

            cred = credentials.Certificate(key_dict)
            firebase_admin.initialize_app(cred)
        else:
            st.warning("Firebase secrets ayarı bulunamadı. Yerel test için .streamlit/secrets.toml dosyasını kontrol edin.")
            
    except Exception as e:
        st.error(f"Firestore bağlantı hatası: {e}. Lütfen Secrets ayarlarını kontrol edin.")

# DB İstemcisi
try:
    db = firestore.client()
except:
    db = None

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
        # Virgülü noktaya çevir ve içindeki sayısal değeri al
        val = str(val).replace(',', '.')
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
    if not text:
        return pd.DataFrame()

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
                    # Çevre tablosunda "Toplam" genellikle sondan önceki değerdir.
                    # Eğer veri karmaşıksa ve sondan çekmek riskliyse en büyük değeri almayı da deneyebiliriz.
                    # Şimdilik kullanıcı formatına göre sondan 2. elemanı hedefliyoruz.
                    if len(numbers) >= 2:
                        val = clean_number(numbers[-2])
                    else:
                        val = clean_number(numbers[0])

                elif value_type == 'en':
                    # En tablosunda Y Mesafe (Genellikle ortalarda)
                    # Kullanıcı örneğine göre Y Mesafe 3. veya 4. blokta
                    idx = 3 if len(numbers) > 3 else len(numbers) - 1
                    val = clean_number(numbers[idx])

                elif value_type == 'boy':
                    # Boy tablosunda X Mesafe
                    idx = 1 if len(numbers) > 1 else 0
                    val = clean_number(numbers[idx])
                
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
    if not text:
        return pd.DataFrame()

    lines = text.strip().split('\n')
    data = []
    
    for line in lines:
        parts = re.split(r'\s+', line.strip())
        if len(parts) >= 4:
            # İlk elemanın beden olup olmadığını kontrol et (Sayı ile başlamamalı)
            if parts[0] and not parts[0][0].isdigit():
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
    if 'form_submitted' not in st.session_state:
        st.session_state['form_submitted'] = False

    st.title("🏭 Kalıp Ölçü Kontrol Sistemi")
    
    # Giriş Simülasyonu
    user = st.sidebar.text_input("Kullanıcı Adı", "operator_1")
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü":
        new_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü")

    # Adım 1: Model Başlatma (İlk Parça ve Genel Bilgiler)
    with st.expander("ℹ️ Model Bilgisi", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"])
        
        # Aktif session varsa bilgileri göster
        if st.session_state.get('active_session'):
            st.info(f"📁 Aktif Model: **{st.session_state['current_model'].get('model_adi')}** | Sezon: **{st.session_state['current_model'].get('sezon')}**")
            st.write(f"Eklenen Parça Sayısı: {len(st.session_state['model_parts'])}")
        else:
            st.caption("İlk parça verisi girildiğinde model bilgileri otomatik oluşacaktır.")

    st.divider()

    col_gerber, col_poly = st.columns([1, 1])

    # --- GERBER GİRİŞLERİ ---
    with col_gerber:
        st.subheader("1. Gerber Verileri")
        g_cevre_txt = st.text_area("Gerber Çevre Tablosu", height=100, key="g_cevre")
        g_en_txt = st.text_area("Gerber En Tablosu (Y Mesafe)", height=100, key="g_en")
        g_boy_txt = st.text_area("Gerber Boy Tablosu (X Mesafe)", height=100, key="g_boy")

    # --- POLYPATTERN GİRİŞİ ---
    with col_poly:
        st.subheader("2. Polypattern Verisi")
        poly_txt = st.text_area("Polypattern Çıktısı", height=340, key="p_main")

    # --- ANALİZ BUTONU ---
    if st.button("Ölçüleri Karşılaştır", type="primary"):
        if not (g_cevre_txt and g_en_txt and g_boy_txt and poly_txt):
            st.warning("Lütfen tüm tabloları yapıştırınız.")
            return

        # 1. Metadata Çıkarma
        metadata = parse_gerber_metadata(g_cevre_txt)
        if metadata:
            current_model_info = {
                "model_adi": metadata['model_adi'],
                "sezon": metadata['sezon'],
                "parca_adi": metadata['parca_adi'],
                "bu": business_unit
            }
            # İlk parça ise session başlat
            if 'active_session' not in st.session_state:
                st.session_state['active_session'] = True
                st.session_state['current_model'] = current_model_info
            else:
                # Sadece parça adını güncelle (Model ve Sezon sabit kalmalı)
                st.session_state['current_model']['parca_adi'] = metadata['parca_adi']
        else:
            st.error("Gerber başlığından Model/Sezon bilgisi okunamadı. Lütfen 'L1/Model-Sezon-Parça' formatının doğruluğunu kontrol edin.")
            return

        # 2. Veri İşleme
        df_g_cevre = parse_gerber_table(g_cevre_txt, 'cevre')
        df_g_en = parse_gerber_table(g_en_txt, 'en')
        df_g_boy = parse_gerber_table(g_boy_txt, 'boy')
        df_poly = parse_polypattern(poly_txt)

        # Tablo boş mu kontrolü
        if any(df.empty for df in [df_g_cevre, df_g_en, df_g_boy, df_poly]):
            st.error("Veriler okunamadı. Lütfen kopyalama formatını kontrol edin.")
            return

        # 3. Birleştirme
        try:
            df_gerber_total = df_g_cevre.merge(df_g_en, on="Beden").merge(df_g_boy, on="Beden")
            df_final = df_gerber_total.merge(df_poly, on="Beden", how="inner")
        except Exception as e:
            st.error(f"Tablolar birleştirilemedi. Beden isimlerinin eşleştiğinden emin olun. Hata: {e}")
            return

        # 4. Fark Hesaplama
        df_final['Fark_Boy'] = df_final['boy'] - df_final['poly_boy']
        df_final['Fark_En'] = df_final['en'] - df_final['poly_en']
        df_final['Fark_Cevre'] = df_final['cevre'] - df_final['poly_cevre']

        # 5. Sonuç Gösterimi
        tolerans = 0.05
        
        def highlight_diff(val):
            color = '#ffcccc' if abs(val) > tolerans else ''
            return f'background-color: {color}'

        st.divider()
        st.subheader(f"Sonuçlar: {st.session_state['current_model']['parca_adi']}")
        
        st.dataframe(df_final.style.format("{:.2f}").map(highlight_diff, subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']))

        hatali_satirlar = df_final[
            (df_final['Fark_Boy'].abs() > tolerans) | 
            (df_final['Fark_En'].abs() > tolerans) | 
            (df_final['Fark_Cevre'].abs() > tolerans)
        ]

        hata_var = not hatali_satirlar.empty
        if hata_var:
            st.error(f"⚠️ DİKKAT: {len(hatali_satirlar)} bedende fark var!")
        else:
            st.success("✅ Tüm ölçüler uyumlu.")

        # -- KAYDETME ALANI (Görünür hale getiriyoruz) --
        st.session_state['temp_result'] = {
            "hata_var": hata_var,
            "hatali_data": hatali_satirlar.to_dict('records') if hata_var else []
        }
        st.session_state['show_save_options'] = True

    # Kaydetme Butonları (Analiz yapıldıysa görünür)
    if st.session_state.get('show_save_options'):
        st.write("---")
        col_btn1, col_btn2 = st.columns([1,4])
        
        with col_btn1:
            if st.button("💾 Parçayı Listeye Ekle"):
                durum = "Hatalı" if st.session_state['temp_result']['hata_var'] else "Doğru"
                
                # Listeye ekle
                part_record = {
                    "parca_adi": st.session_state['current_model']['parca_adi'],
                    "durum": durum,
                    "hata_detayi": st.session_state['temp_result']['hatali_data'],
                    "timestamp": datetime.now()
                }
                st.session_state['model_parts'].append(part_record)
                
                # UI Temizliği için işaretçi
                st.success("Parça eklendi! Sayfa yenileniyor...")
                st.session_state['show_save_options'] = False
                st.rerun()

    # Model Tamamlama Butonu (En az 1 parça eklendiyse)
    if st.session_state.get('active_session') and len(st.session_state['model_parts']) > 0:
        st.divider()
        st.markdown("### 🏁 Modeli Tamamla")
        if st.button("Tüm Parçaları Veritabanına Kaydet", type="primary"):
            save_model_to_db(user, business_unit)

def save_model_to_db(user, bu):
    if not db:
        st.error("Veritabanı bağlantısı kurulamadı. Secrets ayarlarını kontrol edin.")
        return

    model_data = st.session_state['current_model']
    parts = st.session_state['model_parts']
    
    # Genel durum analizi
    genel_durum = "Doğru Çevrilmiş"
    for p in parts:
        if p['durum'] == "Hatalı":
            genel_durum = "Hatalı"
            break
            
    try:
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
        st.success(f"{model_data.get('model_adi')} modeli başarıyla kaydedildi!")
        
        # State sıfırla
        st.session_state['model_parts'] = []
        st.session_state['current_model'] = {}
        del st.session_state['active_session']
        if 'show_save_options' in st.session_state:
            del st.session_state['show_save_options']
            
        st.rerun()
        
    except Exception as e:
        st.error(f"Kayıt sırasında hata oluştu: {e}")

def history_page():
    st.header("📋 Model Kontrol Listesi")
    
    if not db:
        st.warning("Veritabanı bağlı değil. Secrets ayarlarını kontrol ediniz.")
        return

    # Arama
    search_term = st.text_input("🔍 Model Adı veya Kullanıcı Ara")
    
    try:
        # Veriyi çek
        docs = db.collection('qc_records').order_by('tarih', direction=firestore.Query.DESCENDING).limit(50).stream()
        
        data = []
        for doc in docs:
            d = doc.to_dict()
            # Arama filtresi (Client-side filtering for simplicity)
            model_ad = d.get('model_adi', '')
            kullanici = d.get('kullanici', '')
            
            if search_term:
                if search_term.lower() in model_ad.lower() or search_term.lower() in kullanici.lower():
                    data.append(d)
            else:
                data.append(d)
            
        if data:
            df = pd.DataFrame(data)
            # Tarih formatlama
            df['tarih'] = pd.to_datetime(df['tarih']).dt.strftime('%d-%m-%Y %H:%M')
            
            st.dataframe(
                df[['tarih', 'kullanici', 'business_unit', 'model_adi', 'sezon', 'genel_durum', 'parca_sayisi']],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Kayıt bulunamadı.")
            
    except Exception as e:
        st.error(f"Veri çekme hatası: {e}")

if __name__ == "__main__":
    main()