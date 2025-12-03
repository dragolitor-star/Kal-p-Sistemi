import streamlit as st
import pandas as pd
import re
from datetime import datetime
import firebase_admin
from firebase_admin import credentials, firestore
import io

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
# 2. PARSER FONKSİYONLARI (MANUEL GİRİŞ İÇİN)
# --------------------------------------------------------------------------

def parse_gerber_metadata(text_block):
    """Gerber çıktısındaki (L1/UTJW-DW0DW22280-SP26-OBAS) formatından bilgi çeker."""
    if not text_block: return None
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
    """Metni floata çevirir."""
    try:
        if isinstance(val, (int, float)):
            return float(val)
        val = str(val).replace(',', '.')
        found = re.findall(r"[-+]?\d*\.\d+|\d+", val)
        if found:
            return float(found[0])
        return 0.0
    except:
        return 0.0

def parse_gerber_table(text, value_type):
    """Gerber verilerini işler (Manuel metin girişi için)."""
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    size_pattern = r"^(\*?[A-Z0-9]+)\s+(.*)" 

    for line in lines:
        line = line.strip()
        if not line: continue
        
        match = re.match(size_pattern, line)
        if match:
            beden = match.group(1).replace("*", "")
            rest = match.group(2)
            
            if '\t' in rest:
                columns = rest.split('\t')
                columns = [c.strip() for c in columns] 
            else:
                columns = re.split(r'\s+', rest)

            try:
                val = 0.0
                numeric_values = []
                for c in columns:
                    try:
                        if c and any(char.isdigit() for char in c):
                            numeric_values.append(clean_number(c))
                    except:
                        pass

                if value_type == 'cevre':
                    if numeric_values:
                        val = max(numeric_values)
                elif value_type == 'en': 
                    if '\t' in rest and len(columns) >= 4:
                         val = clean_number(columns[3]) 
                    else:
                        if len(numeric_values) >= 3:
                            for v in numeric_values[2:]:
                                if abs(v) > 1.0: 
                                    val = v
                                    break
                            if val == 0.0 and len(numeric_values) > 2:
                                val = numeric_values[2]
                elif value_type == 'boy': 
                     if len(numeric_values) > 1:
                         val = numeric_values[1]

                data.append({"Beden": beden, value_type: abs(val)})
            except:
                continue

    return pd.DataFrame(data)

def parse_polypattern(text):
    """Polypattern temiz tablosunu işler (Manuel metin girişi için)."""
    if not text: return pd.DataFrame()
    lines = text.strip().split('\n')
    data = []
    
    for line in lines:
        clean_line = line.replace("*", " ")
        parts = re.split(r'\s+', clean_line.strip())
        
        if len(parts) >= 4:
            if not parts[0][0].isdigit():
                try:
                    beden = parts[0]
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
# 3. EXCEL PARSER FONKSİYONLARI (OTOMATİK KONTROL İÇİN)
# --------------------------------------------------------------------------

def extract_part_name_from_header(header_text):
    """
    Örnek Header: L1/UTJW-DW0DW22280-SP26-OBAS
    veya sadece UTJW-DW0DW22280-SP26-OBAS
    """
    if not isinstance(header_text, str):
        return None
    
    # Regex: Parça adını (OBAS, A, B vb.) almak için
    # Model ve Sezon formatı: ABC...-SP26-PARCA
    pattern = r"([A-Z0-9]+-[A-Z0-9]+-[A-Z]{2}\d{2}-)([A-Z0-9]+)"
    match = re.search(pattern, header_text)
    if match:
        return match.group(2) # Sadece parça kodunu döndür (OBAS)
    return None

def parse_excel_gerber_sheet(df):
    """
    Gerber sayfasını tarar ve parça parça verileri çıkarır.
    Varsayım: 3 Tablo yan yana durur (Çevre, En, Boy).
    """
    parts_data = {}
    
    # Tablo başlıklarının olduğu satırları bul (İçinde 'Boyut' geçen satırlar)
    # Genellikle başlık satırında birden fazla 'Boyut' olur.
    
    # Tüm dataframe'i satır satır gezmek yerine, 'Boyut' içeren satırları bulalım
    for idx, row in df.iterrows():
        # Satırdaki değerleri stringe çevirip 'Boyut' arayalım
        row_str = row.astype(str).tolist()
        if "Boyut" in row_str:
            # Bu bir başlık satırı olabilir.
            # Yan yana 3 blok olup olmadığını kontrol et.
            indices = [i for i, x in enumerate(row_str) if x == "Boyut"]
            
            if len(indices) >= 3:
                # 3 Blok bulduk: 1. Çevre, 2. En (Y Mes), 3. Boy (X Mes) varsayıyoruz
                # Başlık satırından parça ismini çıkaralım (Genellikle Boyut'un yanındaki hücrede L1/... yazar)
                
                # Blok 1 Başlığı (Çevre)
                header_cell = str(df.iloc[idx, indices[0]+1])
                part_name = extract_part_name_from_header(header_cell)
                
                if not part_name:
                    continue
                    
                # Veri satırlarını oku (Bir sonraki boş satıra veya yeni 'Boyut'a kadar)
                current_row = idx + 1
                part_measurements = []
                
                while current_row < len(df):
                    vals = df.iloc[current_row]
                    beden_raw = str(vals[indices[0]])
                    
                    # Eğer beden hücresi boşsa veya yeni bir başlık geldiyse dur
                    if pd.isna(vals[indices[0]]) or beden_raw == "Boyut" or beden_raw == "nan":
                        break
                        
                    beden = beden_raw.replace("*", "").strip()
                    
                    # --- 1. ÇEVRE (Blok 1) ---
                    # Blok 1'deki sayısal değerlerin maksimumu "Toplam"dır.
                    # Blok 1 aralığı: indices[0] ile indices[1] arası (veya makul bir genişlik)
                    block1_vals = vals[indices[0]+1 : indices[1]].tolist()
                    cevre = 0.0
                    nums1 = [clean_number(x) for x in block1_vals if isinstance(x, (int, float, str))]
                    if nums1:
                        cevre = max(nums1)

                    # --- 2. EN (Blok 2 - Y Mesafe) ---
                    # Manuel fonksiyondaki mantık: Y Mesafe (Genellikle 3. veya 4. sayısal sütun)
                    # Blok 2 aralığı: indices[1] ile indices[2] arası
                    # CSV'de yapı: Boyut, M1, X, XF, Y, YF, Toplam
                    # Y Mesafe, 'Boyut' kolonundan +4 index ötede olabilir.
                    # Daha güvenli: Blok içindeki değerleri al, Y Mesafe mantığını uygula
                    # Y Mesafe (En) genelde büyük değerdir, X Mesafe (Boy) 0'a yakındır.
                    block2_vals = vals[indices[1]+1 : indices[2]].tolist()
                    en = 0.0
                    nums2 = [clean_number(x) for x in block2_vals if isinstance(x, (int, float, str))]
                    
                    # Logic: M1, X, Y... -> Y genelde listedeki 3. elemandır (Index 2) veya sondan öncekidir.
                    # Ancak Excel'den okurken boş hücreler gelebilir.
                    # En güvenlisi: 'Y Mesafe' başlığını bulmak ama satır bazlı gidiyoruz.
                    # Kolon yapısı sabitse: Boyut(0), M1(1), X(2), XF(3), Y(4) -> Index 4
                    try:
                        col_y = indices[1] + 4
                        if col_y < df.shape[1]:
                             en = abs(clean_number(df.iloc[current_row, col_y]))
                    except:
                        pass
                        
                    # --- 3. BOY (Blok 3 - X Mesafe) ---
                    # Blok 3 aralığı: indices[2] sonuna kadar
                    # Kolon yapısı: Boyut(0), M1(1), X(2)... -> Index 2
                    boy = 0.0
                    try:
                        col_x = indices[2] + 2
                        if col_x < df.shape[1]:
                             boy = abs(clean_number(df.iloc[current_row, col_x]))
                    except:
                        pass

                    part_measurements.append({
                        "Beden": beden,
                        "cevre": cevre,
                        "en": en,
                        "boy": boy
                    })
                    
                    current_row += 1
                
                if part_measurements:
                    parts_data[part_name] = pd.DataFrame(part_measurements)

    return parts_data

def parse_excel_pp_sheet(df):
    """
    Polypattern sayfasını tarar.
    Yapı: Parça Adı (Header), Boy, En, Çevre
    """
    parts_data = {}
    
    # 'Boy', 'En', 'Çevre' başlıklarını bul
    for idx, row in df.iterrows():
        row_str = [str(x).strip() for x in row.tolist()]
        
        if "Boy" in row_str and "En" in row_str and "Çevre" in row_str:
            # Başlık satırı bulundu. Parça adı genellikle bu satırın ilk sütunundadır.
            part_header = str(row.iloc[0])
            part_name = extract_part_name_from_header(part_header)
            
            if not part_name:
                # Bazen parça adı header'da olmayabilir, bir üst satırda olabilir mi?
                # Şimdilik header'da olduğunu varsayalım (CSV örneğine göre)
                continue
            
            # Kolon indekslerini bul
            try:
                col_boy = row_str.index("Boy")
                col_en = row_str.index("En")
                col_cevre = row_str.index("Çevre")
            except:
                continue
                
            current_row = idx + 1
            part_measurements = []
            
            while current_row < len(df):
                vals = df.iloc[current_row]
                first_cell = str(vals.iloc[0]).strip()
                
                # Eğer ilk hücre boşsa veya yeni bir başlık geldiyse dur
                if not first_cell or first_cell == "nan" or "Boy" in str(vals.values):
                    # Polypattern çıktısında bazen boş satırlar olur, 
                    # hemen durmak yerine bir sonraki satıra bakmak gerekebilir mi?
                    # CSV örneğinde boş satırlar var.
                    # Eğer beden hücresi boşsa atla, ama döngüyü kırma (hemen bitmesin)
                    # Ama yeni parça başlangıcına kadar nasıl gideceğiz?
                    # Çözüm: Eğer satırda 'Boy' kelimesi varsa break (yeni header).
                    if "Boy" in str(vals.values):
                        break
                    if not first_cell or first_cell == "nan":
                        current_row += 1
                        continue
                
                # Beden satırı mı? (XXS, S * vb.)
                # Sayı ile başlamamalı
                if first_cell and not first_cell[0].isdigit():
                    beden = first_cell.replace("*", "").strip()
                    p_boy = clean_number(vals.iloc[col_boy])
                    p_en = clean_number(vals.iloc[col_en])
                    p_cevre = clean_number(vals.iloc[col_cevre])
                    
                    part_measurements.append({
                        "Beden": beden,
                        "poly_boy": p_boy,
                        "poly_en": p_en,
                        "poly_cevre": p_cevre
                    })
                
                current_row += 1
            
            if part_measurements:
                parts_data[part_name] = pd.DataFrame(part_measurements)
                
    return parts_data


# --------------------------------------------------------------------------
# 4. SAYFA DÜZENİ VE AKIŞ
# --------------------------------------------------------------------------

def main():
    if 'current_model' not in st.session_state:
        st.session_state['current_model'] = {}
    if 'model_parts' not in st.session_state:
        st.session_state['model_parts'] = [] 
    if 'analysis_results' not in st.session_state:
        st.session_state['analysis_results'] = {}

    st.title("🏭 Kalıp Ölçü Kontrol Sistemi")
    st.sidebar.image("https://cdn-icons-png.flaticon.com/512/3022/3022329.png", width=100)
    
    user = st.sidebar.text_input("Kullanıcı Adı", "muhendis_user")
    
    menu = st.sidebar.radio("Menü", ["Yeni Ölçü Kontrolü (Manuel)", "Excel ile Otomatik Kontrol", "Kontrol Listesi / Geçmiş"])

    if menu == "Yeni Ölçü Kontrolü (Manuel)":
        new_control_page(user)
    elif menu == "Excel ile Otomatik Kontrol":
        excel_control_page(user)
    elif menu == "Kontrol Listesi / Geçmiş":
        history_page()

def excel_control_page(user):
    st.header("📂 Excel ile Otomatik Ölçü Kontrolü")
    st.info("Yükleyeceğiniz Excel dosyasında 'GERBER' ve 'PP' (veya Polypattern) verilerini içeren sayfalar olmalıdır. Sistem otomatik olarak parçaları eşleştirip analiz edecektir.")

    col1, col2 = st.columns(2)
    with col1:
        business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"], key="excel_bu")
    
    uploaded_file = st.file_uploader("Excel Dosyasını Yükleyin (.xlsx)", type=["xlsx"])

    if uploaded_file:
        try:
            # Tüm sayfaları oku
            xls = pd.read_excel(uploaded_file, sheet_name=None, header=None)
            sheet_names = list(xls.keys())
            
            st.write(f"Bulunan Sayfalar: {', '.join(sheet_names)}")
            
            # Sayfaları Tahmin Et
            gerber_sheet_name = next((s for s in sheet_names if "GERBER" in s.upper()), None)
            pp_sheet_name = next((s for s in sheet_names if "PP" in s.upper() or "POLY" in s.upper()), None)
            
            # Kullanıcıya seçtir (Tahmin edemezsek veya yanlışsa)
            c1, c2 = st.columns(2)
            with c1:
                selected_gerber = st.selectbox("Gerber Sayfası", sheet_names, index=sheet_names.index(gerber_sheet_name) if gerber_sheet_name else 0)
            with c2:
                selected_pp = st.selectbox("Polypattern Sayfası", sheet_names, index=sheet_names.index(pp_sheet_name) if pp_sheet_name else 0)

            if st.button("🚀 Dosyayı Analiz Et", type="primary"):
                with st.spinner("Veriler işleniyor..."):
                    df_gerber = xls[selected_gerber]
                    df_pp = xls[selected_pp]
                    
                    # Verileri Parse Et
                    gerber_parts = parse_excel_gerber_sheet(df_gerber)
                    pp_parts = parse_excel_pp_sheet(df_pp)
                    
                    if not gerber_parts:
                        st.error("Gerber sayfasında uygun veri bloğu bulunamadı. 'Boyut' başlıklarını kontrol edin.")
                    if not pp_parts:
                        st.error("Polypattern sayfasında uygun veri bloğu bulunamadı. 'Boy', 'En', 'Çevre' başlıklarını kontrol edin.")

                    # Eşleştirme ve Analiz
                    st.session_state['excel_analysis_results'] = []
                    
                    # Model/Sezon bilgisini ilk Gerber parçasından alalım
                    model_adi = "Bilinmiyor"
                    sezon = "Bilinmiyor"
                    
                    # PP'deki parçaları gez, Gerber'de karşılığını bul
                    for part_name, df_p in pp_parts.items():
                        if part_name in gerber_parts:
                            df_g = gerber_parts[part_name]
                            
                            # Merge
                            try:
                                df_final = df_g.merge(df_p, on="Beden", how="inner")
                                
                                # Fark Hesapla
                                df_final['Fark_Boy'] = (df_final['boy'] - df_final['poly_boy']).abs()
                                df_final['Fark_En'] = (df_final['en'] - df_final['poly_en']).abs()
                                df_final['Fark_Cevre'] = (df_final['cevre'] - df_final['poly_cevre']).abs()
                                
                                # Listeye ekle
                                st.session_state['excel_analysis_results'].append({
                                    "parca_adi": part_name,
                                    "df": df_final,
                                    "durum": "Analiz Edildi"
                                })
                            except Exception as e:
                                st.warning(f"{part_name} birleştirilirken hata: {e}")
                        else:
                            st.warning(f"⚠️ {part_name} parçası Polypattern'de var ama Gerber sayfasında bulunamadı.")

                    # Model Adını Gerber dosyasının içeriğinden yakalamaya çalış (İlk header'dan)
                    # Bunun için dosyayı tekrar taramaya gerek yok, ilk parça isminden veya dosya adından çıkarım yapılabilir
                    # Basitlik adına kullanıcı manuel girebilir veya parse edebiliriz.
                    # Şimdilik dosya isminden veya ilk parçadan almaya çalışalım.
                    pass 

                st.success(f"İşlem Tamamlandı! {len(st.session_state['excel_analysis_results'])} parça eşleştirildi.")

        except Exception as e:
            st.error(f"Dosya okunurken hata oluştu: {e}")

    # --- SONUÇLARI GÖSTER VE KAYDET ---
    if st.session_state.get('excel_analysis_results'):
        results = st.session_state['excel_analysis_results']
        
        st.divider()
        st.subheader("📊 Analiz Sonuçları")

        # Toplu Kayıt İçin Hazırlık
        parts_to_save = []
        genel_durum_list = []

        for res in results:
            df_final = res['df']
            parca_adi = res['parca_adi']
            
            tolerans = 0.05
            hatali_satirlar = df_final[
                (df_final['Fark_Boy'] > tolerans) | 
                (df_final['Fark_En'] > tolerans) | 
                (df_final['Fark_Cevre'] > tolerans)
            ]
            hata_var = not hatali_satirlar.empty
            
            status_emoji = "⚠️" if hata_var else "✅"
            genel_durum_list.append("Hatalı" if hata_var else "Doğru")

            with st.expander(f"{status_emoji} {parca_adi}", expanded=hata_var):
                # Tablo
                numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
                existing_cols = [c for c in numeric_cols if c in df_final.columns]
                
                st.dataframe(
                    df_final.style
                    .format("{:.2f}", subset=existing_cols)
                    .map(
                        lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                        subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
                    ),
                    use_container_width=True
                )
                
                if hata_var:
                    st.error(f"{len(hatali_satirlar)} bedende fark tespit edildi.")
                else:
                    st.success("Ölçüler uyumlu.")

            # Kayıt nesnesini hazırla
            part_record = {
                "parca_adi": parca_adi,
                "durum": "Hatalı" if hata_var else "Doğru",
                "hata_detayi": hatali_satirlar[['Beden', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']].to_dict('records') if hata_var else [],
                "timestamp": datetime.now()
            }
            parts_to_save.append(part_record)

        st.markdown("---")
        
        # Model Adı ve Sezonu Manuel Sor (Excel'den tam emin olamazsak)
        c1, c2 = st.columns(2)
        with c1:
            # Otomatik doldurma denemesi (İlk parça isminden L1/... yapısını kullanarak değil, dosya isminden vs.)
            # Kullanıcıya bırakmak en güvenlisi
            model_adi_input = st.text_input("Model Adı (Kaydetmek için giriniz)", placeholder="Örn: UTJW-DW0DW22280")
        with c2:
            sezon_input = st.text_input("Sezon", placeholder="Örn: SP26")

        if st.button("💾 Tüm Sonuçları Veritabanına Kaydet", type="primary", use_container_width=True):
            if not model_adi_input or not sezon_input:
                st.warning("Lütfen Model Adı ve Sezon bilgilerini giriniz.")
                return
            
            if not db:
                st.warning("Veritabanı bağlantısı yok.")
                return
                
            genel_durum = "Hatalı" if "Hatalı" in genel_durum_list else "Doğru Çevrilmiş"
            
            doc_ref = db.collection('qc_records').document()
            doc_ref.set({
                'kullanici': user,
                'tarih': datetime.now(),
                'business_unit': business_unit,
                'model_adi': model_adi_input,
                'sezon': sezon_input,
                'parca_sayisi': len(parts_to_save),
                'genel_durum': genel_durum,
                'parca_detaylari': parts_to_save
            })
            
            st.balloons()
            st.success("Tüm parçalar başarıyla kaydedildi!")
            # State temizle
            st.session_state['excel_analysis_results'] = []
            st.rerun()

def new_control_page(user):
    st.header("Yeni Model Ölçü Kontrolü (Manuel)")

    # --- MODEL BİLGİSİ ---
    with st.expander("ℹ️ İşlem Bilgisi & Model Özeti", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            business_unit = st.selectbox("Business Unit (BU) Seçiniz", ["BU1", "BU3", "BU5"])
            # Kaç parça aynı anda girilecek?
            slot_count = st.number_input("Aynı anda girilecek parça sayısı", min_value=1, max_value=5, value=1, step=1)
        
        with col2:
            if st.session_state.get('active_session'):
                st.info(f"Aktif Model: **{st.session_state['current_model'].get('model_adi')}** | Sezon: **{st.session_state['current_model'].get('sezon')}**")
                
                # Eklenen parçalar
                if len(st.session_state['model_parts']) > 0:
                    st.write("📋 **Eklenen Parçalar:**")
                    for p in st.session_state['model_parts']:
                        durum_ikon = "✅" if p['durum'] == "Doğru" else "❌"
                        st.text(f"{durum_ikon} {p['parca_adi']}")

    st.divider()

    # --- DİNAMİK PARÇA GİRİŞ SLOTLARI ---
    # Tabs kullanarak slotları bölelim, böylece sayfa çok uzamaz
    tabs = st.tabs([f"Parça {i+1}" for i in range(slot_count)])
    
    # Giriş verilerini tutmak için
    inputs = {}

    for i, tab in enumerate(tabs):
        with tab:
            col_gerber, col_poly = st.columns([1, 1])
            with col_gerber:
                st.subheader(f"1. Gerber Verileri (Parça {i+1})")
                inputs[f"g_cevre_{i}"] = st.text_area("Gerber Çevre", height=100, key=f"g_cevre_{i}")
                inputs[f"g_en_{i}"] = st.text_area("Gerber En", height=100, key=f"g_en_{i}")
                inputs[f"g_boy_{i}"] = st.text_area("Gerber Boy", height=100, key=f"g_boy_{i}")
            
            with col_poly:
                st.subheader(f"2. Polypattern Verisi (Parça {i+1})")
                inputs[f"poly_{i}"] = st.text_area("Polypattern Çıktısı", height=340, key=f"poly_{i}")

    st.markdown("---")
    
    # --- TOPLU ANALİZ BUTONU ---
    if st.button("🔍 Tüm Parçaları Analiz Et", type="primary", use_container_width=True):
        # Her bir slotu tek tek analiz et ve sonuçları kaydet
        st.session_state['analysis_results'] = {} # Önceki sonuçları temizle
        
        for i in range(slot_count):
            g_cevre = inputs[f"g_cevre_{i}"]
            g_en = inputs[f"g_en_{i}"]
            g_boy = inputs[f"g_boy_{i}"]
            poly = inputs[f"poly_{i}"]

            # Eğer slot boşsa atla
            if not (g_cevre and g_en and g_boy and poly):
                continue

            # 1. Metadata (Model bilgisi al, ilk dolu parça yeterli)
            # Eğer model bilgisi henüz yoksa ilk dolu parçadan al
            if 'active_session' not in st.session_state:
                metadata = parse_gerber_metadata(g_cevre)
                if metadata:
                    st.session_state['active_session'] = True
                    st.session_state['current_model'] = {
                        "model_adi": metadata['model_adi'],
                        "sezon": metadata['sezon'],
                        "bu": business_unit
                    }

            # Bu parçanın kendi adı (Metadata'dan tekrar çekiyoruz çünkü parça adı değişiyor)
            local_meta = parse_gerber_metadata(g_cevre)
            parca_adi = local_meta['parca_adi'] if local_meta else f"Bilinmeyen Parça {i+1}"

            # 2. Parsing
            df_g_cevre = parse_gerber_table(g_cevre, 'cevre')
            df_g_en = parse_gerber_table(g_en, 'en')
            df_g_boy = parse_gerber_table(g_boy, 'boy')
            df_poly = parse_polypattern(poly)

            if df_g_cevre.empty or df_g_en.empty or df_g_boy.empty or df_poly.empty:
                st.toast(f"Parça {i+1} için veriler okunamadı!", icon="⚠️")
                continue

            try:
                # 3. Merge & Calculate
                df_total = df_g_cevre.merge(df_g_en, on="Beden").merge(df_g_boy, on="Beden")
                df_final = df_total.merge(df_poly, on="Beden", how="inner")
                
                df_final['Fark_Boy'] = (df_final['boy'] - df_final['poly_boy']).abs()
                df_final['Fark_En'] = (df_final['en'] - df_final['poly_en']).abs()
                df_final['Fark_Cevre'] = (df_final['cevre'] - df_final['poly_cevre']).abs()

                # Sonuçları state'e kaydet
                st.session_state['analysis_results'][i] = {
                    "df": df_final,
                    "parca_adi": parca_adi,
                    "saved": False # Henüz kaydedilmedi
                }
            except Exception as e:
                st.toast(f"Parça {i+1} hesaplanırken hata: {e}", icon="❌")

    # --- SONUÇLARI GÖSTERME (HER PARÇA İÇİN AYRI KUTU) ---
    if st.session_state.get('analysis_results'):
        st.subheader("📊 Analiz Sonuçları")
        
        # Sonuçları yine tablarda veya alt alta expanderlarda gösterebiliriz.
        # Kullanıcı "ayrı ayrı kaydet" dediği için alt alta expander daha net görünür.
        
        results = st.session_state['analysis_results']
        
        for i in sorted(results.keys()):
            res = results[i]
            # Eğer bu parça zaten kaydedildiyse gösterme veya "Kaydedildi" de.
            if res.get('saved'):
                continue
                
            df_final = res['df']
            parca_adi = res['parca_adi']
            
            tolerans = 0.05
            hatali_satirlar = df_final[
                (df_final['Fark_Boy'] > tolerans) | 
                (df_final['Fark_En'] > tolerans) | 
                (df_final['Fark_Cevre'] > tolerans)
            ]
            hata_var = not hatali_satirlar.empty
            
            # Kart Görünümü (Expander)
            status_emoji = "⚠️" if hata_var else "✅"
            with st.expander(f"{status_emoji} Sonuç: {parca_adi} (Slot {i+1})", expanded=True):
                
                # Tablo
                numeric_cols = ['boy', 'poly_boy', 'en', 'poly_en', 'cevre', 'poly_cevre', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']
                existing_cols = [c for c in numeric_cols if c in df_final.columns]
                
                st.dataframe(
                    df_final.style
                    .format("{:.2f}", subset=existing_cols)
                    .map(
                        lambda x: 'background-color: #ffcccc' if isinstance(x, (int, float)) and abs(x) > tolerans else '',
                        subset=['Fark_Boy', 'Fark_En', 'Fark_Cevre']
                    ),
                    use_container_width=True
                )

                if hata_var:
                    st.error(f"{len(hatali_satirlar)} bedende fark var.")
                else:
                    st.success("Ölçüler uyumlu.")

                # KAYDET BUTONU
                # Her butonun key'i benzersiz olmalı
                if st.button(f"💾 {parca_adi} - Listeye Ekle", key=f"save_btn_{i}"):
                    part_record = {
                        "parca_adi": parca_adi,
                        "durum": "Hatalı" if hata_var else "Doğru",
                        "hata_detayi": hatali_satirlar[['Beden', 'Fark_Boy', 'Fark_En', 'Fark_Cevre']].to_dict('records') if hata_var else [],
                        "timestamp": datetime.now()
                    }
                    st.session_state['model_parts'].append(part_record)
                    
                    # Bu sonucu "kaydedildi" olarak işaretle ki ekrandan gitsin veya pasif olsun
                    st.session_state['analysis_results'][i]['saved'] = True
                    st.success(f"{parca_adi} eklendi!")
                    st.rerun()

    # --- MODELİ VERİTABANINA YAZMA ---
    if st.session_state.get('active_session') and len(st.session_state['model_parts']) > 0:
        st.markdown("---")
        
        # Kaydedilmemiş analizler var mı uyarısı
        unsaved_count = 0
        if 'analysis_results' in st.session_state:
            unsaved_count = sum(1 for k, v in st.session_state['analysis_results'].items() if not v.get('saved'))
        
        if unsaved_count > 0:
            st.warning(f"⚠️ Yukarıda analiz edilmiş ancak henüz 'Listeye Ekle' denmemiş {unsaved_count} parça var.")

        col_final1, col_final2 = st.columns([3, 1])
        with col_final1:
            st.info(f"**Toplam Eklenen Parça:** {len(st.session_state['model_parts'])}")
        
        with col_final2:
            if st.button("🏁 Tüm Model İşlemini Bitir ve Kaydet", type="primary", use_container_width=True):
                save_to_firestore(user, business_unit)

def save_to_firestore(user, bu):
    if not db:
        st.warning("Veritabanı bağlantısı yok. Simülasyon yapıldı.")
    else:
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
    
    # State Temizleme
    st.session_state['model_parts'] = []
    st.session_state['current_model'] = {}
    st.session_state['analysis_results'] = {}
    del st.session_state['active_session']
    
    # Sayfa yenile (Inputlar temizlensin diye)
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
            # Firestore'dan gelen ham veriyi al
            d = doc.to_dict()
            
            # --- TABLO İÇİN EKSTRA HESAPLAMALAR ---
            parts = d.get('parca_detaylari', [])
            faulty_parts = [p for p in parts if p.get('durum') == 'Hatalı']
            
            # 1. Hatalı Parça Sayısı
            d['hatali_parca_sayisi'] = len(faulty_parts)
            
            # 2. Hata Açıklaması ve Maksimum Sapma
            error_summaries = []
            max_deviation = 0.0
            
            for p in faulty_parts:
                p_name = p.get('parca_adi', 'Parça')
                details = p.get('hata_detayi', [])
                
                # Bu parça için hatalı bedenleri ve farkları topla
                p_errors = []
                for det in details:
                    beden = det.get('Beden', '?')
                    
                    # Hangi ölçülerde hata var?
                    diffs = []
                    # Fark sütunlarının değerlerini kontrol et
                    f_boy = det.get('Fark_Boy', 0)
                    f_en = det.get('Fark_En', 0)
                    f_cevre = det.get('Fark_Cevre', 0)
                    
                    # Tolerans (0.05) üzerindeki farkları açıklamaya ekle
                    if f_boy > 0.05: diffs.append(f"Boy:{f_boy:.2f}")
                    if f_en > 0.05: diffs.append(f"En:{f_en:.2f}")
                    if f_cevre > 0.05: diffs.append(f"Çevre:{f_cevre:.2f}")
                    
                    # Maksimum hata miktarını güncelle
                    current_max = max(f_boy, f_en, f_cevre)
                    if current_max > max_deviation:
                        max_deviation = current_max
                    
                    if diffs:
                        p_errors.append(f"{beden}[{', '.join(diffs)}]")
                
                if p_errors:
                    # Örn: "Pantolon: S[Boy:0.12], M[En:0.08]"
                    error_summaries.append(f"{p_name}: " + " ".join(p_errors))
            
            # Tüm parçaların hata özetlerini birleştir
            d['hata_aciklamasi'] = " | ".join(error_summaries) if error_summaries else "Hata Yok"
            d['maks_hata_miktari'] = max_deviation
            
            data.append(d)
            
        df = pd.DataFrame(data)
        
        if not df.empty:
            if 'tarih' in df.columns:
                df['tarih'] = pd.to_datetime(df['tarih']).dt.strftime('%Y-%m-%d %H:%M')
            if search_term:
                df = df[df['model_adi'].str.contains(search_term, case=False, na=False) | 
                        df['kullanici'].str.contains(search_term, case=False, na=False)]
            
            # Tabloda gösterilecek sütunları ve sırasını belirle
            cols_order = [
                'tarih', 'kullanici', 'business_unit', 'model_adi', 'sezon', 
                'genel_durum', 'parca_sayisi', 'hatali_parca_sayisi', 
                'maks_hata_miktari', 'hata_aciklamasi'
            ]
            
            # Veri setinde olmayan kolonlar varsa hata vermemesi için filtrele
            final_cols = [c for c in cols_order if c in df.columns]
            
            st.dataframe(df[final_cols], use_container_width=True)
            
            # Detay Görünümü
            st.divider()
            selected_row = st.selectbox("Detaylarını görmek istediğiniz modeli seçin:", df['model_adi'].unique())
            if selected_row:
                # Seçilen modelin ilk kaydını al (varsa)
                rows = df[df['model_adi'] == selected_row]
                if not rows.empty:
                    detay = rows.iloc[0]
                    st.write(f"### 🔍 Parça Detayları: {selected_row}")
                    
                    # Parça detaylarını daha şık bir tabloya çevirelim
                    detay_list = detay.get('parca_detaylari', [])
                    if detay_list:
                        detay_df = pd.DataFrame(detay_list)
                        # Timestamp sütununu okunur hale getir
                        if 'timestamp' in detay_df.columns:
                            detay_df['timestamp'] = pd.to_datetime(detay_df['timestamp']).dt.strftime('%H:%M:%S')
                        
                        st.dataframe(
                            detay_df[['parca_adi', 'durum', 'timestamp']],
                            use_container_width=True
                        )
                        
                        # Varsa Hata Detaylarını da JSON olarak değil tablo olarak gösterelim
                        st.write("#### ⚠️ Hata Detayları")
                        for p in detay_list:
                            if p['durum'] == 'Hatalı' and p.get('hata_detayi'):
                                st.caption(f"**{p['parca_adi']}** Hataları:")
                                st.dataframe(pd.DataFrame(p['hata_detayi']))
                    else:
                        st.info("Bu model için parça detayı bulunamadı.")
        else:
            st.info("Kayıt yok.")
    except Exception as e:
        st.error(f"Hata: {e}")

if __name__ == "__main__":
    main()
