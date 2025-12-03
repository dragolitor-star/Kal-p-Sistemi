import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore

# --------------------------------------------------------------------------
# 1. AYARLAR VE FIREBASE BAĞLANTISI
# --------------------------------------------------------------------------
st.set_page_config(page_title="Gerber vs Polypattern Kontrol", layout="wide")

# Firebase başlatma (Secrets kullanarak)
if not firebase_admin._apps:
    try:
        # Secrets verisini al
        key_dict = dict(st.secrets["firebase"])
        
        # Private key içindeki "\n" karakterleri düzelt
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
    db = None 

# --------------------------------------------------------------------------
# 2. PARSER FONKSİYONLARI (GÜNCELLENDİ VE DÜZELTİLDİ)
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    """
    Gerber çıktısındaki (L1/UTJW-DW0DW22280-SP26-OBAS) formatından
    Model, Sezon ve Parça ismini çeker.
    """
    # Regex: L1/ sonrasındaki kodları yakalar.
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
    """Metni floata çevirir, virgülü noktaya dönüştürür."""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).replace(',', '.')
        # Sadece sayısal kısmı (negatif işaret dahil) al
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found:
            return float(found[0])
        return 0.0
    except:
        return 0.0

def parse_gerber_table(text, value_type):
    """
    Gerber verilerini işler.
    value_type: 'cevre', 'en' (Y Mesafe), 'boy' (X Mesafe)
    """
    lines = text.strip().split('\n')
    data = []
    
    # Beden Regex: Satır başındaki XXS, XS, S, *S, M vb. yakalar
    size_pattern = r"^(\*?[A-Z0-9]+)\s+(.*)" 

    for line in lines:
        line = line.strip()
        if not line: continue
        
        match = re.match(size_pattern, line)
        if match:
            beden = match.group(1).replace("*", "") # *S'i S yap
            rest = match.group(2)
            
            # --- TAB İLE AYIRMA KONTROLÜ ---
            # Excel/Gerber'den kopyalanan verilerde genellikle TAB karakteri olur.
            # Tab varsa sütun sırası sabittir, hata payı çok düşüktür.
            if '\t' in rest:
                columns = rest.split('\t')
                # Boşlukları temizle
                columns = [c.strip() for c in columns] 
            else:
                # Tab yoksa mecburen boşluklara göre ayırıyoruz
                columns = re.split(r'\s+', rest)

            try:
                val = 0.0
                
                # İşlem kolaylığı için sadece sayısal değerleri filtreleyip listeye alalım
                numeric_values = []
                for c in columns:
                    try:
                        # Eğer hücrede sayı varsa floata çevirip sakla
                        if c and any(char.isdigit() for char in c):
                            numeric_values.append(clean_number(c))
                    except:
                        pass

                # --- 1. ÇEVRE TABLOSU MANTIĞI ---
                if value_type == 'cevre':
                    # Çevre ölçüsü "Toplam" sütunundadır.
                    # Toplam sütunu, parçaların toplamı olduğu için satırdaki EN BÜYÜK sayıdır.
                    # Bu mantık, aradaki boş hücreler veya sütun kaymalarından etkilenmez.
                    if numeric_values:
                        val = max(numeric_values)
                
                # --- 2. EN TABLOSU (Y MESAFE) MANTIĞI ---
                elif value_type == 'en': 
                    # Tablo yapısı genellikle: M1 | X Mes | X Fark | Y Mes | Y Fark | Toplam
                    # M1 (Index 0)
                    # X Mes (Index 1) - Genelde çok küçük (0.06 gibi)
                    
                    if '\t' in rest and len(columns) >= 4:
                         # Eğer TAB ile ayrılmışsa, Y Mesafe kesinlikle 4. sütundur (index 3).
                         # Çünkü boş hücreler TAB ile korunur.
                         val = clean_number(columns[3]) 
                    else:
                        # Eğer BOŞLUK ile ayrılmışsa, boş hücreler kaybolur.
                        # Heuristic: [M1, X, (XF?), Y, ...]
                        # X Mesafe (Index 1) genelde 0'a yakındır.
                        # Y Mesafe (En) ise M1'e yakın büyüklükte (bazen negatif) bir sayıdır.
                        
                        # Listede M1 ve X'ten sonra gelen (Index 2 ve sonrası)
                        # Mutlak değeri 1'den büyük olan ilk sayıyı Y olarak kabul et.
                        if len(numeric_values) >= 3:
                            for v in numeric_values[2:]:
                                if abs(v) > 1.0: 
                                    val = v
                                    break
                            # Eğer döngüden bir şey çıkmazsa (çok nadir), son çare index 2'yi al
                            if val == 0.0 and len(numeric_values) > 2:
                                val = numeric_values[2]

                # --- 3. BOY TABLOSU (X MESAFE) MANTIĞI ---
                elif value_type == 'boy': 
                     # X Mesafe genellikle M1'den sonraki ilk sayıdır (Index 1).
                     if len(numeric_values) > 1:
                         val = numeric_values[1]

                # Gerber'den gelen değerler negatif olabilir, mutlak değer (abs) alarak kaydediyoruz
                data.append({"Beden": beden, value_type: abs(val)})
            except:
                continue

    return pd.DataFrame(data)

def parse_polypattern(text):
    """
    Polypattern temiz tablosunu işler.
    Yıldız (*) işaretlerini temizleyerek sütun kaymasını önler.
    """
    lines = text.strip().split('\n')
    data = []
    
    for line in lines:
        # Önce * işaretlerini BOŞLUK ile değiştir (S * -> S  )
        # Böylece split yaparken * karakteri ayrı bir sütun gibi davranıp sayıyı 0 yapmaz.
        clean_line = line.replace("*", " ")
        
        parts = re.split(r'\s+', clean_line.strip())
        
        # En az 4 eleman olmalı: Beden, Boy, En, Çevre
        if len(parts) >= 4:
            # İlk elemanın sayı olmadığını kontrol et (Beden ismi olmalı)
            if not parts[0][0].isdigit():
                try:
                    beden = parts[0]
                    # Polypattern çıktısında sıra: Boy, En, Çevre
                    poly_boy = clean_number(parts[1])
                    poly_en = clean_number(parts[2])
                    poly_cevre = clean_number(parts[3])
                    
                    data.append({
                        "Beden": beden,
                        "poly_boy": poly_boy,
                        "poly_en": poly_en,
                        "poly_cevre": poly_cevre
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
    
    user = st.sidebar.text_input("Kullanıcı Adı", "muhendis_user")
    
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü":
        new_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü")

    # Adım 1: Model Bilgileri
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

        # 1. Metadata
        metadata = parse_gerber_metadata(g_cevre_txt)
        if metadata:
            current_model_info = {
                "model_adi": metadata['model_adi'],
                "sezon": metadata['sezon'],
                "parca_adi": metadata['parca_adi'],
                "bu": business_unit
            }
            if 'active_session' not in st.session_state:
                st.session_state['active_session'] = True
                st.session_state['current_model'] = current_model_info
            else:
                st.session_state['current_model']['parca_adi'] = metadata['parca_adi']
        else:
            st.error("Gerber verisinden Model/Sezon bilgisi okunamadı.")
            return

        # 2. Parsing
        df_g_cevre = parse_gerber_table(g_cevre_txt, 'cevre')
        df_g_en = parse_gerber_table(g_en_txt, 'en')
        df_g_boy = parse_gerber_table(g_boy_txt, 'boy')
        df_poly = parse_polypattern(poly_txt)

        if df_g_cevre.empty or df_g_en.empty or df_g_boy.empty or df_poly.empty:
            st.error("Veriler tabloya dönüştürülemedi.")
            return

        try:
            # 3. Birleştirme (Merge)
            df_gerber_total = df_g_cevre.merge(df_g_en, on="Beden").merge(df_g_boy, on="Beden")
            df_final = df_gerber_total.merge(df_poly, on="Beden", how="inner")
            
            # Fark Hesaplama (Mutlak Değer)
            df_final['Fark_Boy'] = (df_final['boy'] - df_final['poly_boy']).abs()
            df_final['Fark_En'] = (df_final['en'] - df_final['poly_en']).abs()
            df_final['Fark_Cevre'] = (df_final['cevre'] - df_final['poly_cevre']).abs()

            st.session_state['last_analysis'] = df_final
            
        except Exception as e:
            st.error(f"Tablo birleştirme hatası: {e}. Lütfen Beden isimlerinin her iki programda da aynı (XXS, M vb.) olduğundan emin olun.")
            return

    # --- SONUÇ EKRANI ---
    if 'last_analysis' in st.session_state and st.session_state['last_analysis'] is not None:
        df_final = st.session_state['last_analysis']
        tolerans = 0.05
        
        hatali_satirlar = df_final[
            (df_final['Fark_Boy'] > tolerans) | 
            (df_final['Fark_En'] > tolerans) | 
            (df_final['Fark_Cevre'] > tolerans)
        ]
        
        hata_var = not hatali_satirlar.empty

        st.divider()
        st.subheader(f"Sonuçlar: {st.session_state['current_model'].get('parca_adi', 'Bilinmeyen Parça')}")
        
        # Tablo Gösterimi (Sayısal format hatası almamak için subset kullanıyoruz)
        numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
        existing_numeric_cols = [col for col in numeric_cols if col in df_final.columns]

        st.dataframe(
            df_final.style
            .format("{:.2f}", subset=existing_numeric_cols)
            .map(
                lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
            )
        )

        if hata_var:
            st.error(f"⚠️ DİKKAT: {len(hatali_satirlar)} bedende ölçü farkı tespit edildi!")
        else:
            st.success("✅ Tüm ölçüler tolerans dahilinde uyumlu.")

        # Kayıt Butonu
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
                
                # Ekranı temizle
                del st.session_state['last_analysis']
                st.success("Parça eklendi!")
                st.rerun()

    # --- MODEL TAMAMLAMA ---
    if st.session_state.get('active_session') and len(st.session_state['model_parts']) > 0:
        st.markdown("---")
        st.subheader("Model İşlemleri")
        if st.button("🏁 Tüm Model Kontrolünü Tamamla ve Veritabanına Yaz", type="primary"):
            save_to_firestore(user, business_unit)

def save_to_firestore(user, bu):
    if not db:
        st.warning("Veritabanı bağlantısı yok. İşlem yerel olarak simüle edildi.")
        st.session_state['model_parts'] = []
        st.session_state['current_model'] = {}
        del st.session_state['active_session']
        if 'last_analysis' in st.session_state: del st.session_state['last_analysis']
        return

    model_data = st.session_state['current_model']
    parts = st.session_state['model_parts']
    
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
    
    # Sıfırla
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
    
    try:
        docs = db.collection('qc_records').order_by('tarih', direction=firestore.Query.DESCENDING).limit(50).stream()
        data = []
        for doc in docs:
            data.append(doc.to_dict())
            
        df = pd.DataFrame(data)
        
        if not df.empty:
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih']).dt.strftime('%Y-%m-%d %H:%M')
            
            if search_term:
                df = df[df['model_adi'].str.contains(search_term, case=False, na=False) | 
                        df['kullanici'].str.contains(search_term, case=False, na=False)]

            st.dataframe(df[['tarih', 'kullanici', 'business_unit', 'model_adi', 'sezon', 'genel_durum', 'parca_sayisi']], use_container_width=True)
            
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
